"""data/manifest.parquet: one row per video found in Vako `.vr/src/<lang>/`. See docs/INTERFACES.md."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.vako import VideoScan, scan_language

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
]


def _prefer(keys: list[str], language: str) -> list[str]:
    # Exact language key first (es beats es-419), then by name, as Vako's chooseTrack does.
    return sorted(keys, key=lambda k: (k.lower() != language.lower(), k))


def row_for(scan: VideoScan) -> dict:
    creator = _prefer([t.key for t in scan.tracks if t.kind == "creator"], scan.language)
    auto = [t.key for t in scan.tracks if t.kind == "auto"]
    auto = sorted(auto, key=lambda k: (not k.lower().endswith("-orig"), k))  # -orig is the recogniser's own
    paths = {t.key: t.path for t in scan.tracks}
    info = scan.info
    return {
        "video_id": scan.video_id,
        "language": scan.language,
        "duration_s": info.duration_s if info else None,
        "channel": info.channel if info else None,
        "channel_id": info.channel_id if info else None,
        "has_creator_track": bool(creator),
        "has_auto_track": bool(auto),
        "creator_track_key": creator[0] if creator else None,
        "creator_track_path": paths[creator[0]] if creator else None,
        "auto_track_key": auto[0] if auto else None,
        "auto_track_path": paths[auto[0]] if auto else None,
        "info_path": str(scan.info_path) if scan.info_path else None,
        "info_source": scan.info_source,
    }


def build_manifest(language: str | None = None, settings: Settings | None = None) -> tuple[pd.DataFrame, Path]:
    """Scan one language and write the manifest. Rows of other languages already in it are kept."""
    settings = settings or get_settings()
    lang = language or settings.language
    scans = scan_language(lang, settings)
    df = pd.DataFrame([row_for(s) for s in scans], columns=COLUMNS)
    df["duration_s"] = df["duration_s"].astype("float64")
    df.attrs["track_kinds"] = dict(Counter(t.kind for s in scans for t in s.tracks))

    path = settings.manifest_path
    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["language"] != lang]
        if len(old):
            kinds = df.attrs["track_kinds"]
            df = pd.concat([old[COLUMNS], df], ignore_index=True)
            df.attrs["track_kinds"] = kinds
    df = df.sort_values(["language", "video_id"]).reset_index(drop=True)
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
        has_info = g["info_source"].notna()
        unknown = has_info.eq(False) | (~g["has_creator_track"] & ~g["has_auto_track"])
        lines.append(f"[{lang}] videos: {len(g)}")
        lines.append(
            f"  info.json: {int(has_info.sum())} "
            f"(vako {int((g['info_source'] == 'vako').sum())}, clipbench {int((g['info_source'] == 'clipbench').sum())})"
        )
        lines.append(f"  with creator track: {int(g['has_creator_track'].sum())}")
        lines.append(f"  with auto track: {int(g['has_auto_track'].sum())}")
        lines.append(f"  only unknown/translation tracks or no info: {int(unknown.sum())}")
        d = g["duration_s"]
        known = d.dropna()
        bins = [0, 5, 30, 60, 90, 180, 600, float("inf")]
        labels = ["<5s", "5-30s", "30-60s", "60-90s", "90-180s", "3-10min", ">10min"]
        if len(known):
            counts = pd.cut(known, bins=bins, labels=labels, right=False).value_counts().reindex(labels)
            lines.append(
                f"  duration (n={len(known)}, unknown {int(d.isna().sum())}): "
                f"median {known.median():.0f}s; " + ", ".join(f"{k} {int(v)}" for k, v in counts.items())
            )
            c = g[g["has_creator_track"]]["duration_s"].dropna()
            elig = c[(c >= 5) & (c <= 90)]
            lines.append(
                f"  creator track and 5-90 s: {len(elig)} clips, "
                f"{g[g['has_creator_track'] & g['duration_s'].between(5, 90)]['channel_id'].nunique()} channels"
            )
        else:
            lines.append(f"  duration: unknown for all {len(d)}")
    kinds = df.attrs.get("track_kinds")
    if kinds:
        lines.append("  track kinds (this scan): " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    return "\n".join(lines)
