"""References and clips. See docs/INTERFACES.md (References, Verified file)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.info import read_json
from clipbench.data.json3 import parse_json3_text, parse_json3_words
from clipbench.data.manifest import load_manifest
from clipbench.data.sample import load_sample
from clipbench.models import Clip, Reference, Tier

log = logging.getLogger(__name__)


def _manifest_row(video_id: str, settings: Settings) -> pd.Series | None:
    try:
        m = load_manifest(settings)
    except FileNotFoundError:
        return None
    rows = m[m["video_id"] == video_id]
    return None if rows.empty else rows.iloc[0]


def _verified(video_id: str, settings: Settings) -> Reference | None:
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
    return Reference(
        video_id=video_id,
        language=data.get("language") or settings.language,
        tier="verified",
        text=text,
        tags=tags,
        audio_sha256=data.get("audio_sha256"),
    )


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


def load_clips(tier: Tier, settings: Settings | None = None, limit: int | None = None) -> list[Clip]:
    """Sample rows (in sample_rank order) with WAV audio and hashes on disk and a reference in `tier`."""
    settings = settings or get_settings()
    try:
        sample = load_sample(settings)
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
        else:
            ref = load_reference(vid, tier, settings)
        if ref is None:
            continue
        if tier == "verified" and ref.audio_sha256 and ref.audio_sha256 != wav_hash:
            log.warning("%s: verified against different audio (%s), skipping", vid, ref.audio_sha256[:12])
            continue
        ref.audio_sha256 = ref.audio_sha256 or wav_hash
        duration = row.get("duration_s")
        if duration is None or pd.isna(duration):
            duration = _wav_duration(wav)
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
