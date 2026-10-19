"""Long-form track (A8): full videos of 3 to 20 minutes, scored for accuracy, timing drift and chunking.

A separate track: its runs live in results/longform/<run_id>/ (numbers) and data/longform/ (audio, text),
never under results/runs/, and its export key is `longform` with `track = "longform"`.

Videos come from the manifest that already exists (no new discovery): the source videos behind window
clips (and any `long` rows), 180 to 1200 s long by their info.json, with a creator caption track for the
whole video. The reference is that creator track (tier `creator`, unverified). Its caption lines carry
start and end times, which are the timing anchors.

Commands: `clipbench longform list | fetch | run | score`. Audio download follows the gentle fetcher
rules of clipbench.data.audio: one video at a time, at least 10 s apart, stop on a bot check or HTTP 429.

Metrics per provider and video (primary metric of the language):

- accuracy: corpus error rate over the whole video, and per minute bucket (each reference token is placed
  in time by spreading a caption line's tokens evenly over the line; insertions count at the reference
  position they are inserted before);
- timing drift: for every caption line whose first token the provider got right, the offset of the
  provider's start time from the line start. Per minute: median absolute and median signed offset. Over
  the video: least squares slope of the signed offset against time (`drift_slope_ms_per_min`, a systematic
  drift) and of the absolute offset (`abs_slope_ms_per_min`, degradation), and the median absolute offset
  before and after minute 5;
- chunking failures: **gaps**, stretches of reference speech longer than GAP_S (10 s) with no hypothesis
  word at all (needs provider word timings), and **duplicated text**, insertion runs of at least
  DUP_MIN_TOKENS tokens (4 words, or 8 characters for unspaced languages) that repeat hypothesis text right
  next to them (within 3 x the run length + 20 tokens before or after), the signature of a chunk boundary transcribed
  twice. Runs that error or time out count in `failure_rate`.
"""

from __future__ import annotations

import dataclasses
import json
import random
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Reference, Word

LONG_MIN_S = 180.0
LONG_MAX_S = 1200.0
GAP_S = 10.0
EARLY_MINUTES = 5
MAX_MINUTE_BUCKET = 19  # minute 19 holds everything from 19:00
GENTLE_GAP_S = 10.0
UNSPACED = {"ja", "zh", "th"}

LONGFORM_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "metric", "status", "duration_s", "ref_len",
    "hyp_len", "errors", "error_rate", "timing_n_words", "timing_median_ms", "timing_p90_ms",
    "timing_median_first5_ms", "timing_median_after5_ms", "drift_slope_ms_per_min", "abs_slope_ms_per_min",
    "n_gaps", "gap_seconds", "longest_gap_s", "dup_events", "dup_tokens", "latency_ms", "rtf",
]  # fmt: skip
MINUTE_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "minute", "ref_tokens", "errors",
    "timing_n_words", "timing_median_abs_ms", "timing_median_signed_ms",
]  # fmt: skip
AGGREGATE_COLUMNS = [
    "provider", "model_id", "tier", "language", "track", "slice_kind", "slice", "n_clips", "error_rate",
    "ci_low", "ci_high", "failure_rate", "audio_hours", "timing_n_words", "timing_median_ms",
    "timing_median_first5_ms", "timing_median_after5_ms", "drift_slope_ms_per_min", "abs_slope_ms_per_min",
    "gaps_per_hour", "gap_share", "dup_events_per_hour", "dup_token_rate",
]  # fmt: skip


# ---------------------------------------------------------------- paths and settings


def data_root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir / "longform"


def audio_dir(settings: Settings | None = None) -> Path:
    return data_root(settings) / "audio"


def results_root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).results_dir / "longform"


@dataclass(frozen=True)
class LongformSettings(Settings):
    """Run directories of the long-form track: results/longform/ and data/longform/runs/."""

    @property
    def runs_dir(self) -> Path:  # type: ignore[override]
        return self.results_dir / "longform"

    @property
    def runs_text_dir(self) -> Path:  # type: ignore[override]
        return self.data_dir / "longform" / "runs"


def longform_settings(settings: Settings | None = None) -> LongformSettings:
    s = settings or get_settings()
    return LongformSettings(**{f.name: getattr(s, f.name) for f in dataclasses.fields(Settings)})


# ---------------------------------------------------------------- candidates


def _read_json(path: Path | str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def candidates(language: str, settings: Settings | None = None) -> pd.DataFrame:
    """Source videos of the manifest, 3 to 20 minutes long, with a whole-video creator track."""
    from clipbench.data.manifest import load_manifest
    from clipbench.data.vako import merged_tracks

    s = settings or get_settings()
    cols = ["video_id", "language", "duration_s", "channel", "channel_id", "category", "platform",
            "creator_track_key", "creator_track_path", "info_path"]  # fmt: skip
    try:
        m = load_manifest(s)
    except FileNotFoundError:
        return pd.DataFrame(columns=cols)
    m = m[(m["language"] == language) & m["has_creator_track"].fillna(False).astype(bool)]
    m = m[m["clip_source"].isin(["window", "long"])]
    tracks = merged_tracks(s, language)
    recs: dict[str, dict[str, Any]] = {}
    for r in m.itertuples(index=False):
        sid = r.source_id if isinstance(r.source_id, str) else str(r.video_id).split("@")[0]
        if sid in recs or (isinstance(r.platform, str) and r.platform != "youtube"):
            continue
        info = _read_json(r.info_path) or {}
        duration = info.get("duration")
        if duration is None and r.clip_source == "long":
            duration = r.duration_s
        if duration is None or not (LONG_MIN_S <= float(duration) <= LONG_MAX_S):
            continue
        key = r.creator_track_key
        if r.clip_source == "long":
            path = r.creator_track_path
        else:
            path = tracks.get(sid, {}).get(key)
        if not path or not Path(path).is_file():
            continue
        recs[sid] = {
            "video_id": sid,
            "language": language,
            "duration_s": float(duration),
            "channel": r.channel if isinstance(r.channel, str) else None,
            "channel_id": r.channel_id if isinstance(r.channel_id, str) else None,
            "category": r.category if isinstance(r.category, str) else None,
            "platform": "youtube",
            "creator_track_key": key,
            "creator_track_path": str(path),
            "info_path": r.info_path if isinstance(r.info_path, str) else None,
        }
    df = pd.DataFrame(list(recs.values()), columns=cols)
    return df.sort_values("video_id").reset_index(drop=True)


def select(cands: pd.DataFrame, n: int | None, seed: int = 7) -> pd.DataFrame:
    """Up to n videos, spread across channels: seeded shuffle, then one per channel per round."""
    if cands.empty or n is None or n >= len(cands):
        return cands.copy()
    rng = random.Random(seed)
    by_channel: dict[str, list[dict]] = {}
    for rec in cands.to_dict("records"):
        by_channel.setdefault(rec.get("channel_id") or rec["video_id"], []).append(rec)
    for recs in by_channel.values():
        rng.shuffle(recs)
    channels = sorted(by_channel)
    rng.shuffle(channels)
    picked: list[dict] = []
    while len(picked) < n and any(by_channel.values()):
        for ch in channels:
            if by_channel[ch] and len(picked) < n:
                picked.append(by_channel[ch].pop())
    return pd.DataFrame(picked, columns=cands.columns).sort_values("video_id").reset_index(drop=True)


def selection_path(language: str, settings: Settings | None = None) -> Path:
    return data_root(settings) / f"selection-{language}.json"


# ---------------------------------------------------------------- audio


@dataclass
class FetchSummary:
    ready: int = 0
    downloaded: int = 0
    failed: dict[str, str] = dataclasses.field(default_factory=dict)
    stopped: str | None = None


def fetch(
    language: str,
    limit: int | None = 10,
    settings: Settings | None = None,
    *,
    seed: int = 7,
    cookies_from_browser: str | None = None,
    gap_s: float = GENTLE_GAP_S,
    downloader: Callable[..., tuple[bool, bool, str]] | None = None,
    converter: Callable[..., tuple[bool, str]] | None = None,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchSummary:
    """Select up to `limit` videos (kept in data/longform/selection-<lang>.json) and fetch their audio."""
    from clipbench.data import audio as audio_mod

    s = settings or get_settings()
    downloader = downloader or audio_mod.download
    converter = converter or audio_mod.to_wav
    sel_path = selection_path(language, s)
    previous = _read_json(sel_path)
    if isinstance(previous, list) and previous and (limit is None or len(previous) >= limit):
        chosen = previous[:limit] if limit is not None else previous
    else:
        chosen = select(candidates(language, s), limit, seed).to_dict("records")
        sel_path.parent.mkdir(parents=True, exist_ok=True)
        sel_path.write_text(json.dumps(chosen, indent=1, ensure_ascii=False))
    adir = audio_dir(s)
    adir.mkdir(parents=True, exist_ok=True)
    hashes_path = adir / "hashes.json"
    hashes: dict = _read_json(hashes_path) or {}
    summary = FetchSummary()
    last: float | None = None
    for i, rec in enumerate(chosen):
        vid = rec["video_id"]
        wav = adir / f"{vid}.wav"
        if wav.is_file() and (hashes.get(vid) or {}).get("wav"):
            summary.ready += 1
            continue
        original = audio_mod.find_original(adir, vid)
        if original is None:
            if last is not None:
                wait = gap_s - (time.monotonic() - last)
                if wait > 0:
                    sleep(wait + random.uniform(0, 3))
            ok, blocked, err = downloader(vid, adir, cookies_from_browser)
            last = time.monotonic()
            if blocked:
                summary.stopped = err
                log(f"stopping: {err}")
                break
            original = audio_mod.find_original(adir, vid)
            if not ok or original is None:
                summary.failed[vid] = err or "no audio file after download"
                log(f"{vid} failed: {summary.failed[vid]}")
                continue
            summary.downloaded += 1
        if not wav.is_file():
            ok, err = converter(original, wav)
            if not ok:
                summary.failed[vid] = f"ffmpeg: {err}"
                continue
        hashes[vid] = {"original": audio_mod.sha256_file(original), "wav": audio_mod.sha256_file(wav)}
        hashes_path.write_text(json.dumps(hashes, indent=1))
        summary.ready += 1
        log(f"[{i + 1}/{len(chosen)}] {vid} ready")
    return summary


def _wav_seconds(path: Path) -> float | None:
    try:
        import soundfile as sf

        return float(sf.info(str(path)).duration)
    except Exception:
        return None


def load_clips(language: str, settings: Settings | None = None) -> list[Clip]:
    """Selected videos with audio on disk, as creator-tier clips whose reference words are caption lines."""
    from clipbench.data.json3 import join_lines, parse_json3_lines

    s = settings or get_settings()
    chosen = _read_json(selection_path(language, s)) or []
    hashes: dict = _read_json(audio_dir(s) / "hashes.json") or {}
    clips: list[Clip] = []
    for rec in chosen:
        vid = rec["video_id"]
        wav = audio_dir(s) / f"{vid}.wav"
        sha = (hashes.get(vid) or {}).get("wav")
        if not wav.is_file() or not sha:
            continue
        try:
            raw = Path(rec["creator_track_path"]).read_text(encoding="utf-8")
            lines = parse_json3_lines(raw, language)
        except (OSError, ValueError):
            continue
        if not lines:
            continue
        duration = _wav_seconds(wav) or float(rec["duration_s"])
        ref = Reference(
            video_id=vid,
            language=language,
            tier="creator",
            text=join_lines(lines, language),
            words=[Word(text=ln.text, start_ms=ln.start_ms, end_ms=ln.end_ms) for ln in lines],
            audio_sha256=sha,
            source_id=vid,
            clip_source="long",
            category=rec.get("category"),
            timing_source="creator_lines",
        )
        clips.append(Clip(video_id=vid, language=language, duration_s=duration, channel=rec.get("channel"),
                          audio_path=str(wav), audio_sha256=sha, reference=ref))  # fmt: skip
    return clips


# ---------------------------------------------------------------- run


def new_run_id(language: str) -> str:
    return f"lf-{language}-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}-{secrets.token_hex(2)}"


async def run_longform(
    language: str,
    providers: list[Any],
    *,
    settings: Settings | None = None,
    run_id: str | None = None,
    clips: list[Clip] | None = None,
    dry_run: bool = False,
    no_cache: bool = False,
    limit: int | None = None,
    console=None,
    progress: bool = True,
):
    """Run providers over the long-form videos. Per instance timeouts are raised to fit the longest video."""
    from clipbench.providers import registry
    from clipbench.runner import run

    s = settings or get_settings()
    clips = clips if clips is not None else load_clips(language, s)
    longest = max((c.duration_s for c in clips), default=0.0)
    instances = []
    for p in providers:
        if isinstance(p, str):
            if p not in registry.REGISTRY:
                raise KeyError(f"unknown provider {p!r}")
            try:
                p = registry.make(p)
            except Exception:
                instances.append(p)  # the runner reports why it cannot load
                continue
        # a 20 minute file takes far longer than a Short: give every call the audio length plus 10 minutes
        p.timeout_s = max(float(type(p).timeout_s), longest + 600.0)
        instances.append(p)
    run_id = run_id or new_run_id(language)
    result = await run(
        instances, "creator", clips=clips, run_id=run_id, no_cache=no_cache, dry_run=dry_run, limit=limit,
        stability=False, settings=longform_settings(s), console=console, progress=progress, language=language,
        seed=s.sample_seed,
    )  # fmt: skip
    if not dry_run and result.meta_path is not None:
        meta = json.loads(result.meta_path.read_text())
        meta.update(kind="longform", track="longform")
        result.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str))
    return result


# ---------------------------------------------------------------- scoring


def _minute(ms: float) -> int:
    return int(min(MAX_MINUTE_BUCKET, max(0, ms // 60000)))


def dup_min_tokens(language: str) -> int:
    return 8 if language.split("-")[0].lower() in UNSPACED else 4


def duplications(ops: list, hyp_tokens: list[str], min_run: int) -> tuple[int, int]:
    """(events, tokens): insertion runs of >= min_run tokens that copy hypothesis text just before them."""
    events = tokens = 0
    run: list[int] = []

    def flush() -> None:
        nonlocal events, tokens
        if len(run) >= min_run:
            start, length = run[0], len(run)
            seq = hyp_tokens[start : start + length]
            reach = 3 * length + 20
            # the alignment may mark either copy as the inserted one: look just before and just after
            windows = (hyp_tokens[max(0, start - reach) : start], hyp_tokens[start + length : start + length + reach])
            if any(w[k : k + length] == seq for w in windows for k in range(0, len(w) - length + 1)):
                events += 1
                tokens += length

    for o in ops:
        if o.op == "ins" and (not run or o.hyp_index == run[-1] + 1):
            run.append(o.hyp_index)
            continue
        flush()
        run = [o.hyp_index] if o.op == "ins" else []
    flush()
    return events, tokens


def gaps(
    ref_lines: list[Word], hyp_words: list[Word] | None, duration_ms: float, *, gap_s: float = GAP_S
) -> tuple[int, float, float] | None:
    """(n_gaps, gap_seconds, longest_gap_s): spans of reference lines lying in a hypothesis-free stretch,
    where the span from the first such line start to the last such line end exceeds gap_s."""
    if hyp_words is None:
        return None
    hyp = sorted(hyp_words, key=lambda w: w.start_ms)
    free: list[tuple[float, float]] = []
    cursor = 0.0
    for w in hyp:
        if w.start_ms > cursor:
            free.append((cursor, float(w.start_ms)))
        cursor = max(cursor, float(w.end_ms))
    end = max(duration_ms, max((ln.end_ms for ln in ref_lines), default=0))
    if end > cursor:
        free.append((cursor, float(end)))
    n, total, longest = 0, 0.0, 0.0
    limit = gap_s * 1000
    for a, b in free:
        if b - a <= limit:
            continue
        inside = [ln for ln in ref_lines if ln.start_ms >= a and ln.end_ms <= b]
        if not inside:
            continue
        span = max(ln.end_ms for ln in inside) - min(ln.start_ms for ln in inside)
        if span > limit:
            n += 1
            total += span / 1000
            longest = max(longest, span / 1000)
    return n, total, longest


def _slope(x: list[float], y: list[float]) -> float | None:
    if len(x) < 10 or (max(x) - min(x)) < 2:
        return None
    return float(np.polyfit(np.asarray(x), np.asarray(y), 1)[0])


@dataclass
class VideoScore:
    row: dict[str, Any]
    minutes: list[dict[str, Any]]


def score_video(
    ref_lines: list[Word], hyp_text: str, hyp_words: list[Word] | None, language: str, duration_s: float
) -> VideoScore:
    from clipbench.scoring.metrics import align
    from clipbench.scoring.normalize import metrics_for
    from clipbench.scoring.timing import timed_tokens

    spec = metrics_for(language)[0]
    ref = timed_tokens(ref_lines, spec.tokenizer)
    hyp_tokens = spec.tokenizer(hyp_text)
    a = align(ref.tokens, hyp_tokens)
    n_ref = len(ref.tokens)
    buckets: dict[int, dict[str, Any]] = {}

    def bucket(m: int) -> dict[str, Any]:
        return buckets.setdefault(m, {"minute": m, "ref_tokens": 0, "errors": 0, "abs": [], "signed": []})

    for t in ref.starts:
        bucket(_minute(t))["ref_tokens"] += 1
    for o in a.ops:
        if o.op == "equal":
            continue
        idx = min(o.ref_index, n_ref - 1)
        when = ref.starts[idx] if n_ref else 0.0
        bucket(_minute(when))["errors"] += 1

    offsets: list[tuple[float, float]] = []
    if hyp_words:
        hyp = timed_tokens(hyp_words, spec.tokenizer)
        a2 = align(ref.tokens, hyp.tokens)
        hit_at = {o.ref_index: o.hyp_index for o in a2.ops if o.op == "equal"}
        for w_index, t_index in ref.first_token.items():
            h = hit_at.get(t_index)
            if h is not None:
                start = ref_lines[w_index].start_ms
                offsets.append((float(start), float(hyp.starts[h] - start)))
    for start, off in offsets:
        b = bucket(_minute(start))
        b["abs"].append(abs(off))
        b["signed"].append(off)

    absolute = [abs(o) for _, o in offsets]
    mins = [s / 60000 for s, _ in offsets]
    early = [abs(o) for s, o in offsets if s < EARLY_MINUTES * 60000]
    late = [abs(o) for s, o in offsets if s >= EARLY_MINUTES * 60000]
    gp = gaps(ref_lines, hyp_words if hyp_words is not None else None, duration_s * 1000)
    dup_e, dup_t = duplications(a.ops, hyp_tokens, dup_min_tokens(language))
    row = {
        "metric": spec.name,
        "ref_len": a.ref_len,
        "hyp_len": len(hyp_tokens),
        "errors": a.errors,
        "error_rate": a.error_rate,
        "timing_n_words": len(offsets) if hyp_words else None,
        "timing_median_ms": float(np.median(absolute)) if absolute else None,
        "timing_p90_ms": float(np.percentile(absolute, 90)) if absolute else None,
        "timing_median_first5_ms": float(np.median(early)) if early else None,
        "timing_median_after5_ms": float(np.median(late)) if late else None,
        "drift_slope_ms_per_min": _slope(mins, [o for _, o in offsets]),
        "abs_slope_ms_per_min": _slope(mins, absolute),
        "n_gaps": gp[0] if gp else None,
        "gap_seconds": gp[1] if gp else None,
        "longest_gap_s": gp[2] if gp else None,
        "dup_events": dup_e,
        "dup_tokens": dup_t,
    }
    minutes = [
        {
            "minute": b["minute"],
            "ref_tokens": b["ref_tokens"],
            "errors": b["errors"],
            "timing_n_words": len(b["abs"]) if hyp_words else None,
            "timing_median_abs_ms": float(np.median(b["abs"])) if b["abs"] else None,
            "timing_median_signed_ms": float(np.median(b["signed"])) if b["signed"] else None,
        }
        for b in sorted(buckets.values(), key=lambda b: b["minute"])
    ]
    return VideoScore(row, minutes)


def _words(value: Any) -> list[Word] | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA or value == "":
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return [Word.model_validate(w) for w in parsed or []] or None
    except (TypeError, ValueError):
        return None


def _median(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.median()) if len(s) else np.nan


def aggregate(scores: pd.DataFrame, minutes: pd.DataFrame, seed: int) -> pd.DataFrame:
    from clipbench.scoring.stats import bootstrap_ci

    records: list[dict[str, Any]] = []
    for (provider, tier), part in scores.groupby(["provider", "tier"], sort=True):
        ok = part[part["status"] == "ok"]
        base = {
            "provider": provider,
            "model_id": ",".join(sorted({str(m) for m in part["model_id"].dropna()})),
            "tier": tier,
            "language": str(part["language"].iloc[0]),
            "track": "longform",
        }
        hours = ok["duration_s"].sum() / 3600
        ci = bootstrap_ci(ok["errors"].to_numpy(float), ok["ref_len"].to_numpy(float), seed=seed)
        timed = ok[ok["n_gaps"].notna()]
        timed_hours = timed["duration_s"].sum() / 3600
        records.append({
            **base, "slice_kind": "all", "slice": "all", "n_clips": int(len(ok)), "error_rate": ci.rate,
            "ci_low": ci.ci_low, "ci_high": ci.ci_high,
            "failure_rate": float((part["status"] != "ok").mean()) if len(part) else np.nan,
            "audio_hours": float(hours), "timing_n_words": float(ok["timing_n_words"].fillna(0).sum()),
            "timing_median_ms": _median(ok["timing_median_ms"]),
            "timing_median_first5_ms": _median(ok["timing_median_first5_ms"]),
            "timing_median_after5_ms": _median(ok["timing_median_after5_ms"]),
            "drift_slope_ms_per_min": _median(ok["drift_slope_ms_per_min"]),
            "abs_slope_ms_per_min": _median(ok["abs_slope_ms_per_min"]),
            "gaps_per_hour": float(timed["n_gaps"].sum() / timed_hours) if timed_hours > 0 else np.nan,
            "gap_share": float(timed["gap_seconds"].sum() / (timed_hours * 3600)) if timed_hours > 0 else np.nan,
            "dup_events_per_hour": float(ok["dup_events"].sum() / hours) if hours > 0 else np.nan,
            "dup_token_rate": float(ok["dup_tokens"].sum() / max(ok["ref_len"].sum(), 1)),
        })  # fmt: skip
        mp = minutes[(minutes["provider"] == provider) & (minutes["tier"] == tier)]
        for minute, g in mp.groupby("minute", sort=True):
            mci = bootstrap_ci(g["errors"].to_numpy(float), g["ref_tokens"].to_numpy(float), seed=seed)
            records.append({
                **base, "slice_kind": "minute", "slice": str(int(minute)), "n_clips": int(len(g)),
                "error_rate": mci.rate, "ci_low": mci.ci_low, "ci_high": mci.ci_high,
                "timing_n_words": float(g["timing_n_words"].fillna(0).sum()),
                "timing_median_ms": _median(g["timing_median_abs_ms"]),
            })  # fmt: skip
    return pd.DataFrame(records, columns=AGGREGATE_COLUMNS)


def score_longform(run_id: str, settings: Settings | None = None) -> Path:
    """Write results/longform/<run_id>/longform.parquet, longform_minutes.parquet, longform_aggregates.parquet."""
    s = settings or get_settings()
    ls = longform_settings(s)
    rows = pd.read_parquet(ls.runs_dir / run_id / "rows.parquet")
    texts = pd.read_parquet(ls.runs_text_dir / run_id / "texts.parquet")
    refs = pd.read_parquet(ls.runs_text_dir / run_id / "refs.parquet")
    text_by = {(t.provider, t.video_id): t for t in texts.itertuples(index=False) if int(t.repeat) == 0}
    ref_by = {r.video_id: r for r in refs.itertuples(index=False)}
    out_rows: list[dict[str, Any]] = []
    out_minutes: list[dict[str, Any]] = []
    for r in rows[rows["repeat"] == 0].itertuples(index=False):
        ref = ref_by.get(r.video_id)
        if ref is None:
            continue
        base = {"provider": r.provider, "model_id": r.model_id, "video_id": r.video_id, "tier": r.tier,
                "language": r.language, "status": r.status, "duration_s": float(r.duration_s),
                "latency_ms": r.latency_ms,
                "rtf": (r.latency_ms / 1000 / r.duration_s) if r.status == "ok" and r.duration_s else None}  # fmt: skip
        if r.status != "ok":
            out_rows.append(base)
            continue
        lines = _words(ref.words_json) or []
        t = text_by.get((r.provider, r.video_id))
        hyp_text = "" if t is None or not isinstance(t.hyp_text, str) else t.hyp_text
        hyp_words = None if t is None else _words(t.words_json)
        vs = score_video(lines, hyp_text, hyp_words, str(r.language), float(r.duration_s))
        out_rows.append({**base, **vs.row})
        for m in vs.minutes:
            out_minutes.append({k: base[k] for k in ("provider", "model_id", "video_id", "tier", "language")} | m)
    scores = pd.DataFrame(out_rows, columns=LONGFORM_COLUMNS)
    for col in LONGFORM_COLUMNS[7:]:
        scores[col] = pd.to_numeric(scores[col], errors="coerce").astype("float64")
    minutes = pd.DataFrame(out_minutes, columns=MINUTE_COLUMNS)
    for col in MINUTE_COLUMNS[5:]:
        minutes[col] = pd.to_numeric(minutes[col], errors="coerce").astype("float64")
    out = results_root(s) / run_id
    out.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(out / "longform.parquet", index=False)
    minutes.to_parquet(out / "longform_minutes.parquet", index=False)
    aggregate(scores, minutes, s.sample_seed).to_parquet(out / "longform_aggregates.parquet", index=False)
    return out / "longform.parquet"


# ---------------------------------------------------------------- export


def _clean_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    recs = df.astype(object).where(df.notna(), None).to_dict("records")
    return [{k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()} for r in recs]


def latest_run(language: str, settings: Settings | None = None) -> str | None:
    root = results_root(settings)
    best: tuple[str, str] | None = None
    for agg in root.glob("*/longform_aggregates.parquet") if root.is_dir() else []:
        meta = _read_json(agg.parent / "run_meta.json") or {}
        if str(meta.get("language", "")).split("-")[0].lower() != language:
            continue
        key = (str(meta.get("updated_at") or meta.get("created_at") or ""), agg.parent.name)
        if best is None or key > best:
            best = key
    return best[1] if best else None


def export_section(run_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """`{"longform": {...}}` for the newest scored long-form run in the run's language, else None."""
    s = settings or get_settings()
    from clipbench.sweeps.export import run_language

    lang = run_language(run_id, s)
    if not lang:
        return None
    lf_id = latest_run(lang, s)
    if lf_id is None:
        return None
    out = results_root(s) / lf_id
    meta = _read_json(out / "run_meta.json") or {}
    agg = pd.read_parquet(out / "longform_aggregates.parquet")
    scores = pd.read_parquet(out / "longform.parquet")
    return {
        "longform": {
            "track": "longform",
            "run_id": lf_id,
            "language": lang,
            "created_at": meta.get("created_at"),
            "reference": "creator captions (unverified)",
            "n_videos": int(scores["video_id"].nunique()),
            "min_minutes": LONG_MIN_S / 60,
            "max_minutes": LONG_MAX_S / 60,
            "aggregates": _clean_records(agg[agg["slice_kind"] == "all"]),
            "minutes": _clean_records(agg[agg["slice_kind"] == "minute"]),
        }
    }


# ---------------------------------------------------------------- CLI


def register(app) -> None:
    import asyncio

    import typer
    from rich.console import Console
    from rich.table import Table

    lf = typer.Typer(help="Long-form track: full videos of 3 to 20 minutes.", no_args_is_help=True)

    @lf.command("list")
    def list_cmd(lang: str = typer.Option(..., "--lang")) -> None:
        """Videos in the manifest that qualify (3 to 20 minutes, whole-video creator track)."""
        df = candidates(lang)
        table = Table(title=f"long-form candidates ({lang}): {len(df)}")
        for col in ("video_id", "minutes", "channel", "category"):
            table.add_column(col)
        for r in df.itertuples(index=False):
            table.add_row(r.video_id, f"{r.duration_s / 60:.1f}", str(r.channel), str(r.category))
        Console().print(table)

    @lf.command("fetch")
    def fetch_cmd(
        lang: str = typer.Option(..., "--lang"),
        limit: int = typer.Option(10, "--limit", help="How many videos to select and download."),
        cookies_from_browser: str | None = typer.Option(None, "--cookies-from-browser"),
    ) -> None:
        """Select videos and download their audio to data/longform/audio/ (one at a time, 10 s apart)."""
        summary = fetch(lang, limit, cookies_from_browser=cookies_from_browser)
        typer.echo(f"ready {summary.ready}, downloaded {summary.downloaded}, failed {len(summary.failed)}")
        if summary.stopped:
            typer.echo(f"stopped: {summary.stopped}")
            raise typer.Exit(2)

    @lf.command("run")
    def run_cmd(
        lang: str = typer.Option(..., "--lang"),
        providers: str = typer.Option(..., "--providers"),
        run_id: str | None = typer.Option(None, "--run-id"),
        dry_run: bool = typer.Option(False, "--dry-run"),
        no_cache: bool = typer.Option(False, "--no-cache"),
        limit: int | None = typer.Option(None, "--limit"),
    ) -> None:
        """Run batch providers over the long-form videos (budget guard and cache as in `clipbench run`)."""
        from clipbench.budget import BudgetExceeded
        from clipbench.providers.registry import resolve
        from clipbench.runner import NoClips

        console = Console(stderr=True)
        try:
            ids = resolve(providers)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        try:
            res = asyncio.run(run_longform(lang, ids, run_id=run_id, dry_run=dry_run, no_cache=no_cache,
                                           limit=limit, console=console))  # fmt: skip
        except (NoClips, BudgetExceeded) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        if dry_run:
            raise typer.Exit(0 if res.allowed else 2)
        typer.echo(res.run_id)

    @lf.command("score")
    def score_cmd(run_id: str = typer.Argument(...)) -> None:
        """Write longform.parquet, longform_minutes.parquet and longform_aggregates.parquet."""
        path = score_longform(run_id)
        agg = pd.read_parquet(path.parent / "longform_aggregates.parquet")
        agg = agg[agg["slice_kind"] == "all"]
        table = Table(title=f"long-form {run_id} (creator captions, unverified)")
        for col in ("provider", "n", "error", "fail", "timing p50", "drift ms/min", "gaps/h", "dups/h"):
            table.add_column(col)

        def f(x, spec):
            return "-" if pd.isna(x) else format(x, spec)

        for r in agg.itertuples(index=False):
            table.add_row(r.provider, str(r.n_clips), f(100 * r.error_rate, ".1f") + "%", f(r.failure_rate, ".2f"),
                          f(r.timing_median_ms, ".0f"), f(r.drift_slope_ms_per_min, ".1f"),
                          f(r.gaps_per_hour, ".2f"), f(r.dup_events_per_hour, ".2f"))  # fmt: skip
        Console().print(table)
        typer.echo(str(path))

    app.add_typer(lf, name="longform")
