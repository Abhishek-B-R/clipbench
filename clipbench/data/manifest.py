"""data/manifest.parquet: one row per clip in `datasets/<lang>/clips.csv`. See docs/INTERFACES.md.

clips.csv says which clips exist. Caption tracks come from whichever importer found them (Vako's
caption store, `data/tracks/`) and are classified from info.json as before (vako.py).

`video_id` is the clip key used everywhere (references, audio files, verified files, caches): the
platform id for a whole video, `<id>@<start_ms>-<end_ms>` for a window. `source_id` is the platform
id. A window row's track paths point at sliced, rebased copies in `data/tracks/windows/`, so anything
that reads a track from the manifest gets the window's captions only.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.datasets import ClipRow, read_clips
from clipbench.data.datasets import guess_category as _guess
from clipbench.data.info import read_json
from clipbench.data.vako import (
    classify_track,
    find_info,
    load_info,
    merged_tracks,
    tracks_dir,
)
from clipbench.data.windows import slice_json3

SHORT_MAX_S = 90.0

COLUMNS = [
    "video_id",
    "language",
    "duration_s",
    "channel",
    "channel_id",
    "has_creator_track",
    "has_auto_track",
    "creator_track_key",
    "creator_track_path",
    "auto_track_key",
    "auto_track_path",
    "info_path",
    "info_source",
    "clip_id",
    "source_id",
    "youtube_id",
    "platform",
    "clip_source",
    "start_ms",
    "end_ms",
    "window_start_s",
    "window_end_s",
    "category",
    "category_guess",
    "license_note",
    "source",
]


def _prefer(keys: list[str], language: str) -> list[str]:
    # Exact language key first (es beats es-419), then by name, as Vako's chooseTrack does.
    return sorted(keys, key=lambda k: (k.lower() != language.lower(), k))


def _raw_info(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = read_json(path, None)
    return data if isinstance(data, dict) else None


def category_hints(settings: Settings, language: str) -> dict[str, str]:
    return read_json(settings.data_dir / "discover" / f"category_hints-{language}.json", {})


def row_for(
    clip: ClipRow,
    language: str,
    tracks: dict[str, Path],
    settings: Settings,
    hints: dict[str, str] | None = None,
) -> dict[str, Any]:
    found = find_info(clip.video_id, settings, language)
    info = load_info(found[0]) if found else None
    if found and info is None:
        found = None
    kinds = {key: classify_track(key, info, language) for key in tracks}
    creator = _prefer([k for k, kind in kinds.items() if kind == "creator"], language)
    auto = sorted([k for k, kind in kinds.items() if kind == "auto"], key=lambda k: (not k.endswith("-orig"), k))
    window = clip.start_ms is not None and clip.end_ms is not None
    if window:
        duration = (clip.end_ms - clip.start_ms) / 1000.0
        source = "window"
    else:
        duration = info.duration_s if info else None
        source = "long" if duration is not None and duration > SHORT_MAX_S else "short"

    category, guessed = clip.category or None, False
    if not category:
        category = (hints or {}).get(clip.video_id) or _guess(_raw_info(found[0] if found else None))
        guessed = category is not None

    row: dict[str, Any] = {
        "video_id": clip.clip_id,
        "language": language,
        "duration_s": duration,
        "channel": info.channel if info else None,
        "channel_id": clip.channel_id or (info.channel_id if info else None),
        "has_creator_track": bool(creator),
        "has_auto_track": bool(auto),
        "creator_track_key": creator[0] if creator else None,
        "creator_track_path": str(tracks[creator[0]]) if creator else None,
        "auto_track_key": auto[0] if auto else None,
        "auto_track_path": str(tracks[auto[0]]) if auto else None,
        "info_path": str(found[0]) if found else None,
        "info_source": found[1] if found else None,
        "clip_id": clip.clip_id,
        "source_id": clip.video_id,
        "youtube_id": clip.video_id if clip.platform == "youtube" else None,
        "platform": clip.platform,
        "clip_source": source,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        "window_start_s": clip.start_ms / 1000.0 if window else None,
        "window_end_s": clip.end_ms / 1000.0 if window else None,
        "category": category,
        "category_guess": guessed,
        "license_note": clip.license_note,
        "source": clip.source or None,
    }
    if window:
        _slice_window_tracks(row, clip, settings)
    return row


def _slice_window_tracks(row: dict[str, Any], clip: ClipRow, settings: Settings) -> None:
    out_dir = tracks_dir(settings) / "windows"
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("creator", "auto"):
        key, path = row[f"{kind}_track_key"], row[f"{kind}_track_path"]
        if not key or not path:
            continue
        try:
            sliced = slice_json3(Path(path).read_text(encoding="utf-8"), clip.start_ms, clip.end_ms)
        except (OSError, ValueError):
            row[f"{kind}_track_key"] = row[f"{kind}_track_path"] = None
            row[f"has_{kind}_track"] = False
            continue
        dst = out_dir / f"{clip.clip_id}.{key}.json3"
        dst.write_text(sliced, encoding="utf-8")
        row[f"{kind}_track_path"] = str(dst)


def build_manifest(language: str | None = None, settings: Settings | None = None) -> tuple[pd.DataFrame, Path]:
    """Build the rows of one language from its clips.csv. Rows of other languages already in the
    manifest are kept."""
    settings = settings or get_settings()
    lang = language or settings.language
    clips = read_clips(lang, settings)
    tracks = merged_tracks(settings, lang)
    hints = category_hints(settings, lang)
    rows = [row_for(c, lang, tracks.get(c.video_id, {}) if c.platform == "youtube" else {}, settings, hints) for c in clips]
    df = pd.DataFrame(rows, columns=COLUMNS)
    for col in ("duration_s", "window_start_s", "window_end_s"):
        df[col] = df[col].astype("float64")
    for col in ("start_ms", "end_ms"):
        df[col] = df[col].astype("Int64")
    df["category_guess"] = df["category_guess"].astype(bool)

    path = settings.manifest_path
    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["language"] != lang]
        if len(old):
            df = pd.concat([old.reindex(columns=COLUMNS), df], ignore_index=True)
    df = df.sort_values(["language", "video_id"]).reset_index(drop=True)
    df.attrs["clips_csv_rows"] = len(clips)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    _load.cache_clear()
    return df, path


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


def load_manifest(settings: Settings | None = None) -> pd.DataFrame:
    settings = settings or get_settings()
    path = settings.manifest_path
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `clipbench data build` first")
    return _load(str(path), _mtime(path))


def summarize(df: pd.DataFrame, language: str | None = None) -> str:
    if language:
        df = df[df["language"] == language]
    lines = []
    for lang, g in df.groupby("language"):
        src = g["clip_source"].fillna("short")
        lines.append(
            f"[{lang}] clips: {len(g)} (short {int((src == 'short').sum())}, window {int((src == 'window').sum())}, "
            f"long {int((src == 'long').sum())}); platforms: "
            + ", ".join(f"{k} {v}" for k, v in Counter(g["platform"].fillna("youtube")).most_common())
        )
        lines.append(
            f"  with creator track: {int(g['has_creator_track'].sum())}, with auto track: "
            f"{int(g['has_auto_track'].sum())}, no info.json: {int(g['info_source'].isna().sum())}"
        )
        lines.append(f"  channels: {g['channel_id'].nunique()}")
        cats = Counter(g["category"].fillna("(none)"))
        lines.append(
            "  categories: "
            + ", ".join(f"{k} {v}" for k, v in sorted(cats.items()))
            + f" ({int(g['category_guess'].sum())} guessed)"
        )
        lines.append(
            "  by source: " + ", ".join(f"{k} {v}" for k, v in Counter(g["source"].fillna("?")).most_common())
        )
        d = g["duration_s"].dropna()
        if len(d):
            lines.append(f"  duration: median {d.median():.0f}s, min {d.min():.0f}s, max {d.max():.0f}s")
    return "\n".join(lines) or "no clips"
