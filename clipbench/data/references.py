"""References and clips. See docs/INTERFACES.md (References, Verified file) and docs/V2.md.

Every Reference carries the clip's v2 fields from the manifest row: platform, source_id (platform id),
clip_source, start_ms, category. A verified reference also carries the reviewer's category,
nonspeech_spans, codeswitch_spans, punctuated and the v3 fields (conditions, speaker_turns,
entity_spans, alternatives, itn_spans; [] when an older file has none), and word timings from `clipbench.align`
(`data/align/<clip_id>.json`, only when made from the verified text against the current WAV;
timing_source `aligner:<name>@<version>`) or, failing that, the creator track's timings when that track
is timed by the word (`creator`). Timings are relative to the clip start (window tracks are rebased).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.info import read_json
from clipbench.data.json3 import parse_json3_text, parse_json3_words
from clipbench.data.manifest import load_manifest
from clipbench.data.sample import load_sample
from clipbench.models import Clip, Reference, Tier, Word

log = logging.getLogger(__name__)


def _manifest_row(video_id: str, settings: Settings) -> pd.Series | None:
    try:
        m = load_manifest(settings)
    except FileNotFoundError:
        return None
    rows = m[m["video_id"] == video_id]
    return None if rows.empty else rows.iloc[0]


def _s(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


def clip_fields(row: pd.Series | None, video_id: str) -> dict:
    """The v2 Reference fields of a clip from its manifest or sample row."""
    if row is None:
        return {"source_id": video_id.split("@")[0], "clip_source": "window" if "@" in video_id else "short"}
    start = row.get("start_ms")
    if start is None or pd.isna(start):
        ws = row.get("window_start_s")
        start = None if ws is None or pd.isna(ws) else int(round(float(ws) * 1000))
    return {
        "platform": _s(row.get("platform")) or "youtube",
        "source_id": _s(row.get("source_id")) or _s(row.get("youtube_id")) or video_id.split("@")[0],
        "clip_source": _s(row.get("clip_source")) or "short",
        "start_ms": None if start is None else int(start),
        "category": _s(row.get("category")),
    }


def _creator_words(row: pd.Series | None) -> list[Word] | None:
    if row is None or not isinstance(row.get("creator_track_path"), str):
        return None
    try:
        return parse_json3_words(Path(row["creator_track_path"]).read_text(encoding="utf-8")) or None
    except OSError:
        return None


def _wav_sha(video_id: str, settings: Settings) -> str | None:
    return (read_json(settings.audio_dir / "hashes.json", {}).get(video_id) or {}).get("wav")


def _text_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()  # same as clipbench.align.text_sha256


def _aligned(video_id: str, settings: Settings, text: str) -> tuple[list[Word] | None, str | None]:
    """Forced alignment words (clip relative) from `clipbench.align`, used only when the alignment was
    made from this verified text (`text_sha256`) against the WAV that is on disk now (`audio_sha256`)."""
    meta = read_json(settings.data_dir / "align" / f"{video_id}.json", None)
    if not isinstance(meta, dict) or meta.get("tier", "verified") != "verified":
        return None, None
    wav_sha = _wav_sha(video_id, settings)
    if not wav_sha or meta.get("audio_sha256") != wav_sha:
        return None, None
    if meta.get("text_sha256") != _text_sha(text):
        return None, None
    try:
        from clipbench.align import load_alignment  # lazy: align imports this module

        loaded = load_alignment(video_id, settings)
    except (ImportError, ValueError, OSError):
        return None, None
    if not loaded or not loaded[1]:
        return None, None
    aligner, words = loaded
    return words, f"aligner:{aligner}"


def _verified(video_id: str, settings: Settings, row: pd.Series | None = None) -> Reference | None:
    path = settings.verified_dir / f"{video_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable verified file %s", path)
        return None
    tags = list(data.get("tags") or [])
    if data.get("skipped") or "unusable" in tags:
        return None
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    row = row if row is not None else _manifest_row(video_id, settings)
    fields = clip_fields(row, video_id)
    if _s(data.get("category")):
        fields["category"] = data["category"]
    words, timing = _aligned(video_id, settings, text)
    if words is None:
        words = _creator_words(row)
        timing = "creator" if words else None
    try:
        return Reference(
            video_id=video_id,
            language=data.get("language") or settings.language,
            tier="verified",
            text=text,
            tags=tags,
            audio_sha256=data.get("audio_sha256"),
            words=words,
            timing_source=timing,
            nonspeech_spans=data.get("nonspeech_spans") or [],
            codeswitch_spans=data.get("codeswitch_spans") or [],
            punctuated=bool(data.get("punctuated")),
            # v3 (docs/V3.md); code point offsets; old files have none
            conditions=list(data.get("conditions") or []),
            speaker_turns=data.get("speaker_turns") or [],
            entity_spans=data.get("entity_spans") or [],
            alternatives=data.get("alternatives") or [],
            itn_spans=data.get("itn_spans") or [],
            **fields,
        )
    except ValueError as exc:
        log.warning("bad verified file %s: %s", path, exc)
        return None


def _creator(video_id: str, settings: Settings, row: pd.Series | None = None) -> Reference | None:
    row = row if row is not None else _manifest_row(video_id, settings)
    if row is None or not bool(row.get("has_creator_track")):
        return None
    path = row.get("creator_track_path")
    if not isinstance(path, str):
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
        text = parse_json3_text(raw, row["language"])  # creator text is left as written
    except (OSError, ValueError):
        log.warning("unreadable creator track %s", path)
        return None
    if not text.strip():
        return None
    words = parse_json3_words(raw)
    return Reference(
        video_id=video_id,
        language=row["language"],
        tier="creator",
        text=text,
        words=words or None,
        timing_source="creator" if words else None,
        **clip_fields(row, video_id),
    )


def load_reference(video_id: str, tier: Tier, settings: Settings | None = None) -> Reference | None:
    settings = settings or get_settings()
    if tier == "verified":
        return _verified(video_id, settings)
    if tier == "creator":
        return _creator(video_id, settings)
    raise ValueError(f"unknown tier {tier!r}")


def _wav_duration(path: Path) -> float | None:
    try:
        import soundfile

        return float(soundfile.info(str(path)).duration)
    except Exception:  # any unreadable WAV just has no duration
        return None


def load_clips(
    tier: Tier, settings: Settings | None = None, limit: int | None = None, language: str | None = None
) -> list[Clip]:
    """Sample rows (by language, then sample_rank) with WAV audio and hashes on disk and a reference in
    `tier`. `language` restricts to one language; default all languages in the sample."""
    settings = settings or get_settings()
    try:
        sample = load_sample(settings, language)
    except FileNotFoundError:
        return []
    hashes: dict = read_json(settings.audio_dir / "hashes.json", {})
    clips: list[Clip] = []
    for _, row in sample.iterrows():
        if limit is not None and len(clips) >= limit:
            break
        vid = row["video_id"]
        wav = settings.audio_dir / f"{vid}.wav"
        wav_hash = (hashes.get(vid) or {}).get("wav")
        if not wav.is_file() or not wav_hash:
            continue
        if tier == "creator":
            ref = _creator(vid, settings, row)
        elif tier == "verified":
            ref = _verified(vid, settings, row)
        else:
            ref = load_reference(vid, tier, settings)
        if ref is None:
            continue
        if tier == "verified" and ref.audio_sha256 and ref.audio_sha256 != wav_hash:
            log.warning("%s: verified against different audio (%s), skipping", vid, ref.audio_sha256[:12])
            continue
        ref.audio_sha256 = ref.audio_sha256 or wav_hash
        duration = _wav_duration(wav)  # the real length; a window's WAV may differ from end - start
        if duration is None:
            duration = row.get("duration_s")
            duration = None if duration is None or pd.isna(duration) else duration
        if duration is None:
            continue
        channel = row.get("channel")
        clips.append(
            Clip(
                video_id=vid,
                language=row["language"],
                duration_s=float(duration),
                channel=channel if isinstance(channel, str) else None,
                audio_path=str(wav),
                audio_sha256=wav_hash,
                reference=ref,
            )
        )
    return clips
