"""`clipbench run-stream`: stream clips through realtime providers, through the budget guard and the cache.

A streaming run is its own run kind, never mixed with batch numbers:

- results/runs/<run_id>/stream_rows.parquet   one row per (provider, clip), numbers and labels only
- results/runs/<run_id>/run_meta.json         `kind = "stream"`, chunk size, speed, providers, prices
- data/runs/<run_id>/stream_texts.parquet     final text, final words and every event (never published)
- data/runs/<run_id>/refs.parquet             the reference snapshot the run was scored against

Cache: `ResultCache` keyed like batch (provider id, model id, audio sha256, settings hash) where the provider
id ends in `-stream` and the settings carry `kind = "stream"`, the chunk size and the speed. The cached
Result keeps the whole StreamTranscript in `raw_response`, so a cache hit replays the recorded timings.

While the run is in progress finished rows go to `stream_rows.part.jsonl` / `stream_texts.part.jsonl`;
rerunning with the same run id skips rows that are done (ok, or skipped for a reason other than the budget).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console

from clipbench.budget import (
    BUDGET_ERROR,
    BudgetGuard,
    Estimate,
    ProviderEstimate,
    check_can_start,
    load_spend,
)
from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Result
from clipbench.providers.base import ProviderSkip
from clipbench.providers.cache import ResultCache
from clipbench.streaming.base import Clock, Connect, StreamingProvider, StreamTranscript
from clipbench.streaming.metrics import StreamMetrics, clip_metrics, final_text, final_words
from clipbench.streaming.pacer import Pacer, read_pcm
from clipbench.streaming.session import FINAL_TIMEOUT_S, stream_audio

KIND = "stream"
METRIC_COLUMNS = list(StreamMetrics.__dataclass_fields__)
STREAM_ROW_COLUMNS = [
    "run_id", "kind", "provider", "model_id", "provider_version", "video_id", "language", "tier",
    "status", "error", "cached", "timed_out", "duration_s", "audio_seconds", "cost_usd", "chunk_ms", "speed",
    "session_s", "connect_ms", "audio_end_ms", "closed_ms", "category", "conditions", "tags",
    *METRIC_COLUMNS,
]  # fmt: skip
TEXT_COLUMNS = ["provider", "video_id", "final_text", "words_json", "transcript_json"]
REF_COLUMNS = ["video_id", "tier", "ref_text", "words_json", "tags", "category", "conditions"]

Key = tuple[str, str]


class NoClips(ValueError):
    """Nothing to stream."""


class MixedLanguages(ValueError):
    """The clips are in more than one language."""


class UnverifiedProviders(ValueError):
    """Every streaming provider left for a real run was refused by the verification gate."""


def clip_conditions(clip: Clip) -> list[str]:
    """Audio conditions (docs/V3.md): the reference's own, plus `music` implied by the background_music
    tag or a music non-speech span."""
    ref = clip.reference
    out = set(ref.conditions or [])
    if "background_music" in ref.tags or any(s.kind == "music" for s in ref.nonspeech_spans):
        out.add("music")
    return sorted(out)


@dataclass
class StreamRunResult:
    run_id: str
    dry_run: bool
    estimate: Estimate
    spent_before: float
    cap: float
    allowed: bool
    skipped_providers: dict[str, str] = field(default_factory=dict)
    rows_path: Path | None = None
    texts_path: Path | None = None
    meta_path: Path | None = None
    rows: pd.DataFrame | None = None
    refused_providers: dict[str, str] = field(default_factory=dict)
    allowed_unverified: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def resolve_providers(specs: list[str | StreamingProvider]) -> tuple[list[StreamingProvider], dict[str, str]]:
    from clipbench.streaming import registry

    ok: list[StreamingProvider] = []
    skipped: dict[str, str] = {}
    for spec in specs:
        if isinstance(spec, StreamingProvider):
            p = spec
        else:
            try:
                p = registry.make(spec)
            except KeyError:
                raise
            except Exception as exc:  # noqa: BLE001
                skipped[spec] = f"{spec}: skipped, could not load adapter ({type(exc).__name__}: {exc})"
                continue
        available, message = p.availability()
        if available and p.paid and p.price_per_minute() <= 0:
            available, message = False, f"{p.id}: skipped, paid provider with no price for {p.model_id} in pricing.toml"
        if available:
            ok.append(p)
        else:
            skipped[p.id] = message
    return ok, skipped


def configure(p: StreamingProvider, chunk_ms: int, speed: float) -> None:
    """Chunk size and speed change what a streaming provider returns, so they are part of the cache key."""
    p.options["chunk_ms"] = int(chunk_ms)
    p.options["speed"] = float(speed)


def is_billable(p: StreamingProvider) -> bool:
    return bool(p.paid) or p.price_per_minute() > 0


def estimate_stream(
    providers: list[StreamingProvider], clips: list[Clip], *, speed: float = 1.0, cache: ResultCache | None = None
) -> Estimate:
    """Upper bound of a streaming run: every clip once per provider, priced per streaming minute
    (session billed providers include `session_overhead_s`). Clips a provider cannot stream and cache hits
    cost nothing. No stability reruns in the streaming track."""
    out = Estimate()
    for p in providers:
        usd = 0.0
        audio = 0.0
        cached = 0
        n = 0
        for c in clips:
            try:
                p.check_clip(c.language, c.duration_s)
            except ProviderSkip:
                continue
            n += 1
            audio += c.duration_s
            if not is_billable(p):
                continue
            if cache is not None and cache.get(p.id, p.model_id, c.audio_sha256, p.settings_hash()):
                cached += 1
                continue
            usd += p.estimate_cost(c.duration_s, speed)
        out.providers[p.id] = ProviderEstimate(
            provider=p.id, model_id=p.model_id, n_clips=n, audio_seconds=audio, main_usd=usd,
            stability_clips=0, stability_repeats=0, stability_usd=0.0, cached_requests=cached,
        )  # fmt: skip
    return out


def _atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _atomic_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _read_parquet_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pd.read_parquet(path).astype(object).where(lambda d: d.notna(), None).to_dict("records")


def rows_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=STREAM_ROW_COLUMNS)
    for col in ("cached", "timed_out"):
        df[col] = df[col].fillna(False).astype(bool)
    ints = ("chunk_ms", "n_partials", "n_finals", "n_segments", "partial_tokens", "rewritten_tokens",
            "final_tokens", "revisions", "ref_len", "hits", "subs", "dels", "ins", "errors", "timing_n_words",
            "hyp_chars")  # fmt: skip
    for col in ints:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    floats = ("duration_s", "audio_seconds", "cost_usd", "speed", "session_s", "connect_ms", "audio_end_ms",
              "closed_ms", "ttfp_ms", "ttfs_ms", "partial_rewrite_rate", "revisions_per_word", "error_rate",
              "timing_median_ms", "timing_p90_ms", "timing_within_200ms")  # fmt: skip
    for col in floats:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("run_id", "kind", "provider", "model_id", "provider_version", "video_id", "language", "tier",
                "status", "error", "category", "conditions", "tags", "metric"):
        df[col] = df[col].astype("string")
    return df.sort_values(["provider", "video_id"]).reset_index(drop=True)


def texts_frame(texts: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(texts, columns=TEXT_COLUMNS)
    for col in TEXT_COLUMNS:
        df[col] = df[col].astype("string")
    return df.sort_values(["provider", "video_id"]).reset_index(drop=True)


def refs_frame(clips: list[Clip]) -> pd.DataFrame:
    recs = []
    for c in clips:
        ref = c.reference
        recs.append(
            {
                "video_id": c.video_id,
                "tier": ref.tier,
                "ref_text": ref.text,
                "words_json": None
                if ref.words is None
                else json.dumps([w.model_dump() for w in ref.words], ensure_ascii=False),
                "tags": json.dumps(list(ref.tags), ensure_ascii=False),
                "category": ref.category,
                "conditions": json.dumps(clip_conditions(c)),
            }
        )
    df = pd.DataFrame(recs, columns=REF_COLUMNS)
    for col in REF_COLUMNS:
        df[col] = df[col].astype("string")
    return df.sort_values("video_id").reset_index(drop=True)


def _scrub(message: str | None, settings: Settings) -> str | None:
    from clipbench.runner import _scrub_paths

    return _scrub_paths(message, settings)


def print_stream_plan(
    console: Console, est: Estimate, providers, skipped, spent: float, cap: float,
    refused: dict[str, str] | None = None, overridden: dict[str, str] | None = None,
) -> bool:  # fmt: skip
    from clipbench.runner import print_plan

    return print_plan(
        console, est, providers, skipped, spent, cap, title="clipbench streaming run plan (dry run, no network)",
        refused=refused, overridden=overridden,
    )  # fmt: skip


def _print_locked(
    console: Console, skipped: dict[str, str], clips: list[Clip], chunk_ms: int, speed: float,
    allow: frozenset[str] = frozenset(),
) -> None:  # fmt: skip
    """What the providers that are only missing keys would cost, from pricing.toml."""
    from rich.table import Table

    from clipbench.providers.verification import check
    from clipbench.runner import _print_refusal_summary, _verification_notes
    from clipbench.streaming import registry

    locked = []
    for pid in skipped:
        if pid in registry.STREAM_REGISTRY:
            p = registry.make(pid)
            configure(p, chunk_ms, speed)
            if is_billable(p):
                locked.append(p)
    if not locked:
        return
    extra = estimate_stream(locked, clips, speed=speed)
    refused, overridden = check(locked, allow)
    table = Table(title="if you add the missing keys (streaming estimate from pricing.toml)")
    for col in ("provider", "model", "billing", "usd/min", "clips", "est cost", "note"):
        table.add_column(col)
    for p in locked:
        e = extra.providers[p.id]
        table.add_row(p.id, p.model_id, p.billing, f"{p.price_per_minute():.5f}", str(e.n_clips),
                      f"${e.total_usd:.4f}", ", ".join(_verification_notes(p.id, refused, overridden)))  # fmt: skip
    console.print(table)
    _print_refusal_summary(console, refused)
    console.print(f"total with every streaming provider: ${extra.total_usd:.4f}")


def transcript_result(p: StreamingProvider, t: StreamTranscript, cost: float) -> Result:
    return Result(
        text=final_text(t.events),
        words=final_words(t.events),
        latency_ms=float(t.closed_ms),
        audio_seconds=float(t.audio_seconds),
        cost_usd=float(cost),
        raw_response=t.model_dump(mode="json"),
        model_id=p.model_id,
        provider_version=t.meta.get("model_version"),
    )


# ---------------------------------------------------------------- run


async def run_stream(
    providers: list[str | StreamingProvider],
    tier: str = "verified",
    *,
    language: str | None = None,
    clips: list[Clip] | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    no_cache: bool = False,
    chunk_ms: int = 100,
    speed: float = 1.0,
    concurrency: int | None = None,
    final_timeout_s: float = FINAL_TIMEOUT_S,
    settings: Settings | None = None,
    console: Console | None = None,
    connect: Connect | None = None,
    clock: Clock | None = None,
    quiet_s: float | None = None,
    allow_unverified: str | list[str] | frozenset[str] | None = None,
) -> StreamRunResult:
    """Stream `clips` (default: load_clips(tier, language)) through `providers`.

    `connect`, `clock` and `quiet_s` are test hooks (in-memory server, fake clock, short quiet period).
    A real run refuses a paid provider whose price or data policy is not documented in pricing.toml unless its
    id is in `allow_unverified` (recorded in run_meta); a dry run prices it and marks it "would be refused".
    """
    from clipbench.providers.verification import check, parse_allow, refused_message

    settings = settings or get_settings()
    console = console or Console(stderr=True)
    if clips is None:
        from clipbench.data.references import load_clips

        clips = load_clips(tier, settings=settings, language=language)
    if language:
        base = language.split("-")[0].lower()
        clips = [c for c in clips if c.language.split("-")[0].lower() == base]
    langs = sorted({c.language for c in clips})
    if len(langs) > 1:
        raise MixedLanguages(f"a run holds one language, but the clips are in {', '.join(langs)}; pass --lang")
    clips = sorted(clips, key=lambda c: c.video_id)
    if limit is not None:
        clips = clips[:limit]

    active, skipped = resolve_providers(providers)
    for msg in skipped.values():
        console.print(f"[yellow]{msg}[/yellow]")
    allow = parse_allow(allow_unverified)
    refused, overridden = check(active, allow)
    if not dry_run and refused:
        active = [p for p in active if p.id not in refused]
        for pid, reason in refused.items():
            skipped[pid] = refused_message(pid, reason)
            console.print(f"[red]{skipped[pid]}[/red]")
        if not active:
            raise UnverifiedProviders(
                "refusing to start: every streaming provider was refused by the verification gate "
                f"({'; '.join(refused_message(pid, r) for pid, r in refused.items())})"
            )
    for pid, reason in overridden.items():
        console.print(f"[yellow]{pid}: unverified ({reason}), allowed by --allow-unverified[/yellow]")
    for p in active:
        configure(p, chunk_ms, speed)
    if speed != 1.0:
        console.print(f"[yellow]speed {speed}: latency numbers are not comparable with real-time runs[/yellow]")

    cache = ResultCache(settings.cache_dir)
    est = estimate_stream(active, clips, speed=speed, cache=None if no_cache else cache)
    spent_before = float(load_spend(settings.spend_path)["total_usd"])
    cap = settings.budget_usd
    from clipbench.runner import new_run_id

    run_id = run_id or new_run_id()

    if dry_run:
        allowed = print_stream_plan(console, est, active, skipped, spent_before, cap, refused, overridden)
        _print_locked(console, skipped, clips, chunk_ms, speed, allow)
        return StreamRunResult(run_id, True, est, spent_before, cap, allowed, skipped,
                               refused_providers=refused, allowed_unverified=overridden)  # fmt: skip
    if not clips:
        raise NoClips(f"no clips to stream (tier {tier}{', language ' + language if language else ''})")
    check_can_start(est, cap, spent_before)

    rows_dir = settings.runs_dir / run_id
    text_dir = settings.runs_text_dir / run_id
    rows_path = rows_dir / "stream_rows.parquet"
    rows_part = rows_dir / "stream_rows.part.jsonl"
    texts_path = text_dir / "stream_texts.parquet"
    texts_part = text_dir / "stream_texts.part.jsonl"
    refs_path = text_dir / "refs.parquet"
    meta_path = rows_dir / "run_meta.json"
    if (rows_dir / "rows.parquet").exists():
        raise ValueError(f"run {run_id} is a batch run; streaming rows never share a run with batch rows")
    rows_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    prev_rows = {(r["provider"], r["video_id"]): r for r in _read_parquet_records(rows_path) + _read_jsonl(rows_part)}
    prev_texts = {
        (t["provider"], t["video_id"]): t for t in _read_parquet_records(texts_path) + _read_jsonl(texts_part)
    }
    done = {
        k
        for k, r in prev_rows.items()
        if (r.get("status") == "ok" and k in prev_texts) or (r.get("status") == "skipped" and r.get("error") != BUDGET_ERROR)
    }

    guard = BudgetGuard(settings.spend_path, cap, run_id)
    lock = asyncio.Lock()
    new_rows: dict[Key, dict[str, Any]] = {}
    new_texts: dict[Key, dict[str, Any]] = {}
    stopped = {p.id: False for p in active}
    sems = {p.id: asyncio.Semaphore(max(1, int(concurrency or p.concurrency))) for p in active}

    def base_row(p: StreamingProvider, c: Clip) -> dict[str, Any]:
        return {
            "run_id": run_id, "kind": KIND, "provider": p.id, "model_id": p.model_id, "provider_version": None,
            "video_id": c.video_id, "language": c.language, "tier": c.tier, "status": "ok", "error": None,
            "cached": False, "timed_out": False, "duration_s": c.duration_s, "audio_seconds": c.duration_s,
            "cost_usd": 0.0, "chunk_ms": chunk_ms, "speed": speed, "category": c.reference.category,
            "conditions": json.dumps(clip_conditions(c)), "tags": json.dumps(list(c.tags), ensure_ascii=False),
        }  # fmt: skip

    async def emit(row: dict[str, Any], text: dict[str, Any] | None) -> None:
        key = (row["provider"], row["video_id"])
        row["error"] = _scrub(row.get("error"), settings)
        async with lock:
            if text is not None:
                new_texts[key] = text
                with texts_part.open("a") as fh:
                    fh.write(json.dumps(text, ensure_ascii=False) + "\n")
            new_rows[key] = row
            with rows_part.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    async def finish(row: dict[str, Any], c: Clip, result: Result, cached: bool) -> None:
        t = StreamTranscript.model_validate(result.raw_response)
        m = clip_metrics(t, c.reference.text, c.reference.words, c.language)
        row.update(
            status="ok", cached=cached, model_id=result.model_id or row["model_id"],
            provider_version=result.provider_version, cost_usd=float(result.cost_usd),
            audio_seconds=float(t.audio_seconds), timed_out=bool(t.timed_out), session_s=t.session_s,
            connect_ms=t.connect_ms, audio_end_ms=t.audio_end_ms, closed_ms=t.closed_ms, **m.as_dict(),
        )  # fmt: skip
        text = {
            "provider": row["provider"],
            "video_id": row["video_id"],
            "final_text": result.text,
            "words_json": None
            if result.words is None
            else json.dumps([w.model_dump() for w in result.words], ensure_ascii=False),
            "transcript_json": json.dumps(result.raw_response, ensure_ascii=False),
        }
        await emit(row, text)

    async def job(p: StreamingProvider, c: Clip) -> None:
        key = (p.id, c.video_id)
        if key in done:
            return
        row = base_row(p, c)
        try:
            p.check_clip(c.language, c.duration_s)
        except ProviderSkip as exc:
            row.update(status="skipped", error=str(exc))
            await emit(row, None)
            return
        shash = p.settings_hash()
        if not no_cache:
            hit = cache.get(p.id, p.model_id, c.audio_sha256, shash)
            if hit is not None:
                await finish(row, c, hit, cached=True)
                return
        billable = is_billable(p)
        async with sems[p.id]:
            if stopped[p.id]:
                row.update(status="skipped", error=BUDGET_ERROR)
                await emit(row, None)
                return
            reserved = p.estimate_cost(c.duration_s, speed) if billable else 0.0
            if billable and not await guard.reserve(reserved):
                if not stopped[p.id]:
                    console.print(f"[red]{p.id}: stopping, the next session would cross the ${cap:.2f} cap[/red]")
                stopped[p.id] = True
                row.update(status="skipped", error=BUDGET_ERROR)
                await emit(row, None)
                return
            t0 = time.perf_counter()
            timeout = c.duration_s / speed + final_timeout_s + 60.0
            try:
                pcm = read_pcm(c.audio_path, p.sample_rate)
                pacer = Pacer(chunk_ms=chunk_ms, speed=speed, sample_rate=p.sample_rate)
                if clock is not None:
                    pacer.clock = clock
                transcript = await asyncio.wait_for(
                    stream_audio(p, pcm, c.language, pacer=pacer, connect=connect,
                                 final_timeout_s=final_timeout_s, quiet_s=quiet_s),
                    timeout=timeout,
                )  # fmt: skip
            except TimeoutError:
                if billable:  # the socket was open, so the session may have been billed
                    await guard.record(p.id, c.video_id, reserved, reserved)
                row.update(status="error", timed_out=True, error=f"timeout after {timeout:.0f}s",
                           closed_ms=(time.perf_counter() - t0) * 1000.0)
                await emit(row, None)
                return
            except Exception as exc:  # noqa: BLE001
                if billable:
                    # Audio may have been streamed (and billed) before the failure: count the estimate.
                    await guard.record(p.id, c.video_id, reserved, reserved)
                row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:500],
                           closed_ms=(time.perf_counter() - t0) * 1000.0)
                await emit(row, None)
                return
            cost = p.actual_cost(transcript) if billable else 0.0
            if billable:
                await guard.record(p.id, c.video_id, cost, reserved)
            result = transcript_result(p, transcript, cost)
            cache.put(p.id, p.model_id, c.audio_sha256, shash, result)
            await finish(row, c, result, cached=False)

    await asyncio.gather(*(job(p, c) for p in active for c in clips))

    all_rows = {**prev_rows, **new_rows}
    all_texts = {**prev_texts, **new_texts}
    all_texts = {k: v for k, v in all_texts.items() if all_rows.get(k, {}).get("status") == "ok"}
    rows_df = rows_frame(list(all_rows.values()))
    _atomic_parquet(rows_df, rows_path)
    _atomic_parquet(texts_frame(list(all_texts.values())), texts_path)
    refs_df = refs_frame(clips)
    if refs_path.exists():
        old = pd.read_parquet(refs_path)
        keep = old[~old["video_id"].isin(set(refs_df["video_id"]))]
        if len(keep):
            refs_df = pd.concat([refs_df, keep.reindex(columns=REF_COLUMNS).astype("string")], ignore_index=True)
    _atomic_parquet(refs_df.sort_values("video_id").reset_index(drop=True), refs_path)
    rows_part.unlink(missing_ok=True)
    texts_part.unlink(missing_ok=True)

    from clipbench import __version__
    from clipbench.runner import _git_info, _package_versions

    old_meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            old_meta = json.loads(meta_path.read_text())
        except ValueError:
            old_meta = {}
    commit, dirty = _git_info(settings.root)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "run_id": run_id,
        "kind": KIND,
        "created_at": old_meta.get("created_at", now),
        "updated_at": now,
        "git_commit": commit,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "packages": {**_package_versions(), **_ws_version()},
        "clipbench_version": __version__,
        "providers": [provider_meta(p) for p in active]
        + [
            m
            for m in old_meta.get("providers") or []
            if isinstance(m, dict) and m.get("id") not in {p.id for p in active}
            and m.get("id") in set(rows_df["provider"].astype(str))
        ],
        "skipped_providers": {pid: _scrub(msg, settings) for pid, msg in skipped.items()},
        "verification_refused": refused,
        "allow_unverified": overridden,
        "budget_stopped": [pid for pid, s in stopped.items() if s],
        "tier": tier,
        "language": clips[0].language if clips else language,
        "n_clips": int(rows_df["video_id"].nunique()) if len(rows_df) else len(clips),
        "chunk_ms": chunk_ms,
        "speed": speed,
        "real_time": speed == 1.0,
        "no_cache": no_cache,
        "estimate_usd": est.total_usd,
        "spent_before_usd": spent_before,
        "spent_after_usd": guard.spent,
        "budget_cap_usd": cap,
    }
    _atomic_json(meta, meta_path)
    return StreamRunResult(
        run_id=run_id, dry_run=False, estimate=est, spent_before=spent_before, cap=cap, allowed=True,
        skipped_providers=skipped, rows_path=rows_path, texts_path=texts_path, meta_path=meta_path, rows=rows_df,
        refused_providers=refused, allowed_unverified=overridden,
    )  # fmt: skip


def _ws_version() -> dict[str, str]:
    import importlib.metadata

    try:
        return {"websockets": importlib.metadata.version("websockets")}
    except importlib.metadata.PackageNotFoundError:
        return {}


def provider_meta(p: StreamingProvider) -> dict[str, Any]:
    from clipbench.providers.verification import verification

    pricing = p.pricing()
    v = verification(p.id)
    return {
        "id": p.id,
        "model_id": p.model_id,
        "settings": p.settings(),
        "settings_hash": p.settings_hash(),
        "paid": bool(p.paid),
        "uploads_audio": bool(p.uploads_audio),
        "billing": p.billing,
        "usd_per_minute": p.price_per_minute(),
        "pricing_checked_on": pricing.get("checked_on"),
        "pricing_checked_url": pricing.get("checked_url"),
        "live_verified": v.live_verified,
        "price_status": v.price_status,
        "policy_status": v.policy_status,
    }
