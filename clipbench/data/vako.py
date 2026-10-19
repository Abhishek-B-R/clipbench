"""Scan Vako's caption store and classify every track from info.json.

Vako keeps `<videoId>.<trackkey>.json3` (and sometimes `<videoId>.info.json`) in
`<vako_root>/.vr/src/<lang>/`. Video ids are 11 characters and may start with `-`.

A track's kind is decided from info.json only, never from its file name:

- key listed (with at least one format) in info `subtitles` -> `creator`
- key listed in info `automatic_captions`:
  - ends with `-orig` -> `auto` (YouTube ASR of the spoken language)
  - plain key whose base is the clip language, and info `language` says the clip is spoken in it
    (or info has no language and no `-orig` track in another language) -> `auto`
  - anything else -> `auto_translation` (YouTube's machine translation of ASR from another language)
- key in neither, or no info.json -> `unknown`

This mirrors Vako `chooseTrack` / `parseInfo` (a key only counts when its track list is non empty).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipbench.config import Settings, get_settings
from clipbench.models import Track, TrackKind

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
ORIG_SUFFIX = "-orig"


@dataclass
class InfoSummary:
    """The fields of a yt-dlp info.json that clipbench needs."""

    video_id: str | None
    title: str | None
    duration_s: float | None
    language: str | None
    channel: str | None
    channel_id: str | None
    subtitle_keys: list[str] = field(default_factory=list)
    automatic_keys: list[str] = field(default_factory=list)


def _keys_of(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [k for k, tracks in value.items() if isinstance(tracks, list) and len(tracks) > 0]


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def summarize_info(data: dict[str, Any]) -> InfoSummary:
    duration = data.get("duration")
    return InfoSummary(
        video_id=_str(data.get("id")),
        title=_str(data.get("title")),
        duration_s=float(duration) if isinstance(duration, int | float) and not isinstance(duration, bool) else None,
        language=_str(data.get("language")),
        channel=_str(data.get("channel")) or _str(data.get("uploader")),
        channel_id=_str(data.get("channel_id")) or _str(data.get("uploader_id")),
        subtitle_keys=_keys_of(data.get("subtitles")),
        automatic_keys=_keys_of(data.get("automatic_captions")),
    )


def load_info(path: Path) -> InfoSummary | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return summarize_info(data) if isinstance(data, dict) else None


def base_of(key: str) -> str:
    return key.lower().split("-")[0]


def is_original(key: str) -> bool:
    return key.lower().endswith(ORIG_SUFFIX)


def classify_track(key: str, info: InfoSummary | None, language: str) -> TrackKind:
    """The kind of one caption track, decided from info.json alone."""
    if info is None:
        return "unknown"
    wanted = language.lower()
    if key in info.subtitle_keys:
        return "creator"
    if key not in info.automatic_keys:
        return "unknown"
    if base_of(key) != wanted:
        return "auto_translation"
    if is_original(key):
        return "auto"
    spoken = base_of(info.language) if info.language else None
    if spoken == wanted:
        return "auto"
    spoken_elsewhere = any(is_original(k) and base_of(k) != wanted for k in info.automatic_keys)
    if spoken is None and not spoken_elsewhere:
        return "auto"
    return "auto_translation"


def scan_tracks(src_dir: Path) -> dict[str, dict[str, Path]]:
    """`{video_id: {track_key: path}}` for every json3 file in a Vako source dir."""
    found: dict[str, dict[str, Path]] = {}
    if not src_dir.is_dir():
        return found
    for path in sorted(src_dir.iterdir()):
        name = path.name
        if not name.endswith(".json3"):
            continue
        stem = name[: -len(".json3")]
        video_id, sep, key = stem[:11], stem[11:12], stem[12:]
        if sep != "." or not key or not VIDEO_ID.match(video_id):
            continue
        found.setdefault(video_id, {})[key] = path
    return found


def find_info(video_id: str, settings: Settings | None = None, language: str | None = None) -> tuple[Path, str] | None:
    """Vako's info.json first, then the one clipbench fetched. Returns (path, source)."""
    settings = settings or get_settings()
    lang = language or settings.language
    vako = settings.vako_root / ".vr" / "src" / lang / f"{video_id}.info.json"
    if vako.is_file():
        return vako, "vako"
    ours = settings.info_dir / f"{video_id}.info.json"
    if ours.is_file():
        return ours, "clipbench"
    return None


@dataclass
class VideoScan:
    video_id: str
    language: str
    tracks: list[Track]
    info: InfoSummary | None
    info_path: Path | None
    info_source: str | None


def tracks_dir(settings: Settings) -> Path:
    """json3 tracks clipbench downloaded itself (discover, fetch-tracks). Flat: `<id>.<key>.json3`."""
    return settings.data_dir / "tracks"


def merged_tracks(settings: Settings, language: str) -> dict[str, dict[str, Path]]:
    """Vako's `.vr/src/<lang>/` merged with `data/tracks/` (keys of this language only).

    Where both have the same key, Vako's file is kept.
    """
    merged = scan_tracks(settings.vako_root / ".vr" / "src" / language)
    for video_id, files in scan_tracks(tracks_dir(settings)).items():
        for key, path in files.items():
            if base_of(key) != language.lower():
                continue
            merged.setdefault(video_id, {}).setdefault(key, path)
    return dict(sorted(merged.items()))


def scan_language(language: str | None = None, settings: Settings | None = None) -> list[VideoScan]:
    settings = settings or get_settings()
    lang = language or settings.language
    out: list[VideoScan] = []
    for video_id, files in merged_tracks(settings, lang).items():
        found = find_info(video_id, settings, lang)
        info = load_info(found[0]) if found else None
        if found and info is None:
            found = None  # unreadable info.json counts as none
        tracks = [
            Track(video_id=video_id, language=lang, key=key, kind=classify_track(key, info, lang), path=str(path))
            for key, path in sorted(files.items())
        ]
        out.append(
            VideoScan(
                video_id=video_id,
                language=lang,
                tracks=tracks,
                info=info,
                info_path=found[0] if found else None,
                info_source=found[1] if found else None,
            )
        )
    return out
