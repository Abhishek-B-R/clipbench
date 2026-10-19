"""data/sample.parquet: a stratified sample of creator-captioned clips, Shorts first.

Eligible Shorts: `clip_source` "short", a creator track and 5 <= duration_s <= 90. Eligible windows:
`clip_source` "window" with a creator track (20-60 s by construction, one per long video). No
channel may be more than 10% of the target size (cap = floor(0.10 * n), at least 1), counting Shorts
and windows together. Shorts are taken first, round robin over channels in a seeded order, one clip
per channel per round; windows then fill up to n the same way with what is left of each channel's
cap. `shorts_only` skips the windows. If the pool cannot reach n under the cap, every clip the cap
allows is taken and the result says so.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.manifest import COLUMNS, load_manifest

MIN_DURATION_S = 5.0
MAX_DURATION_S = 90.0
MAX_CHANNEL_SHARE = 0.10


def channel_cap(n: int) -> int:
    return max(1, math.floor(MAX_CHANNEL_SHARE * n))


def _source(m: pd.DataFrame) -> pd.Series:
    if "clip_source" in m:
        return m["clip_source"].fillna("short")
    return pd.Series("short", index=m.index)


def eligible(manifest: pd.DataFrame, *, shorts_only: bool = False) -> pd.DataFrame:
    m = manifest
    creator = m["has_creator_track"].fillna(False).astype(bool)
    src = _source(m)
    shorts = creator & (src == "short") & m["duration_s"].between(MIN_DURATION_S, MAX_DURATION_S)
    windows = creator & (src == "window") & m["duration_s"].notna()
    return m[shorts if shorts_only else shorts | windows]


def _channel_key(row: pd.Series) -> str:
    for col in ("channel_id", "channel"):
        v = row.get(col)
        if isinstance(v, str) and v:
            return v
    return "<no channel>"


def _round_robin(
    pool: pd.DataFrame, rng: random.Random, n_left: int, taken: dict[str, int], cap: int
) -> list[int]:
    by_channel: dict[str, list[int]] = {}
    for ch in sorted(pool["_channel"].unique()):
        idx = sorted(pool.index[pool["_channel"] == ch], key=lambda i: pool.at[i, "video_id"])
        rng.shuffle(idx)
        by_channel[ch] = idx[: max(0, cap - taken.get(ch, 0))]
    order = sorted(by_channel)
    rng.shuffle(order)
    picked: list[int] = []
    depth = 0
    while len(picked) < n_left:
        added = False
        for ch in order:
            if depth < len(by_channel[ch]) and len(picked) < n_left:
                picked.append(by_channel[ch][depth])
                taken[ch] = taken.get(ch, 0) + 1
                added = True
        if not added:
            break
        depth += 1
    return picked


def stratified_sample(manifest: pd.DataFrame, n: int, seed: int, *, shorts_only: bool = False) -> pd.DataFrame:
    pool = eligible(manifest, shorts_only=shorts_only).copy()
    cols = [c for c in COLUMNS if c in pool.columns]
    if pool.empty:
        out = pool[cols].copy()
        out["sample_rank"] = pd.Series(dtype="int64")
        return out
    pool["_channel"] = pool.apply(_channel_key, axis=1)
    src = _source(pool)
    rng = random.Random(seed)
    cap = channel_cap(n)
    taken: dict[str, int] = {}
    picked = _round_robin(pool[src == "short"], rng, n, taken, cap)
    if not shorts_only and len(picked) < n:
        picked += _round_robin(pool[src == "window"], rng, n - len(picked), taken, cap)
    out = pool.loc[picked, cols].copy()
    out["sample_rank"] = range(len(out))
    return out.reset_index(drop=True)


@dataclass
class SampleResult:
    df: pd.DataFrame
    path: Path
    n_target: int
    n_eligible: int
    message: str


def write_sample(
    n: int = 150,
    seed: int | None = None,
    language: str | None = None,
    settings: Settings | None = None,
    *,
    shorts_only: bool = False,
) -> SampleResult:
    settings = settings or get_settings()
    seed = settings.sample_seed if seed is None else seed
    language = language or settings.language
    manifest = load_manifest(settings)
    manifest = manifest[manifest["language"] == language]
    pool = eligible(manifest, shorts_only=shorts_only)
    df = stratified_sample(manifest, n, seed, shorts_only=shorts_only)
    path = settings.sample_path
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)

    channels = df["channel_id"].fillna(df["channel"]).nunique() if len(df) else 0
    top = df["channel_id"].fillna(df["channel"]).value_counts().max() if len(df) else 0
    src_df, src_pool = _source(df), _source(pool)
    lines = [
        f"wrote {path}: {len(df)} clips ({int((src_df == 'short').sum())} Shorts, "
        f"{int((src_df == 'window').sum())} windows) from {channels} channels "
        f"(seed {seed}, cap {channel_cap(n)} per channel, largest channel {top})",
        f"eligible pool: {int((src_pool == 'short').sum())} Shorts with a creator track and "
        f"{MIN_DURATION_S:.0f}-{MAX_DURATION_S:.0f} s, {int((src_pool == 'window').sum())} windows, "
        f"{pool['channel_id'].fillna(pool['channel']).nunique() if len(pool) else 0} channels",
    ]
    if len(df) < n:
        lines.append(
            f"SHORT: asked for {n} but only {len(df)} clips are available under the per channel cap. "
            f"The sample is smaller than requested."
        )
    return SampleResult(df=df, path=path, n_target=n, n_eligible=len(pool), message="\n".join(lines))


def load_sample(settings: Settings | None = None) -> pd.DataFrame:
    settings = settings or get_settings()
    if not settings.sample_path.exists():
        raise FileNotFoundError(f"{settings.sample_path} not found; run `clipbench data sample` first")
    return pd.read_parquet(settings.sample_path).sort_values("sample_rank").reset_index(drop=True)


def refresh_sample(settings: Settings | None = None) -> pd.DataFrame | None:
    """Re-read each sampled clip's manifest columns (after a rebuild added tracks). Keeps the
    sample's membership and `sample_rank` unchanged."""
    settings = settings or get_settings()
    if not settings.sample_path.exists():
        return None
    sample = load_sample(settings)
    manifest = load_manifest(settings).drop_duplicates("video_id").set_index("video_id")
    cols = [c for c in COLUMNS if c != "video_id" and c in manifest.columns]
    fresh = sample[["video_id", "sample_rank"]].join(manifest[cols], on="video_id")
    missing = fresh[cols[0]].isna() & sample[cols[0]].notna()
    fresh.loc[missing, cols] = sample.loc[missing, cols]  # a clip gone from the manifest keeps its old row
    fresh = fresh[[c for c in COLUMNS if c in fresh.columns] + ["sample_rank"]]
    fresh.to_parquet(settings.sample_path, index=False)
    return fresh


def youtube_url(youtube_id: str, clip_source: str | None = None, start_s: float | None = None) -> str:
    """Shorts link for a Short; watch link with `t=<start>s` for a window."""
    if clip_source == "window" and start_s is not None and not pd.isna(start_s):
        return f"https://www.youtube.com/watch?v={youtube_id}&t={int(start_s)}s"
    return f"https://www.youtube.com/shorts/{youtube_id}"


def clip_meta(settings: Settings | None = None) -> dict[str, dict]:
    """Per clip key (`video_id`): youtube_id, kind ("short"|"window"), start_s, end_s, youtube_url.

    Read from data/sample.parquet. For other modules (review UI, runner refs, scoring slices, export)
    that need to know where a clip comes from without reading the parquet schema themselves.
    """
    settings = settings or get_settings()
    if not settings.sample_path.exists():
        return {}
    s = pd.read_parquet(settings.sample_path)
    out: dict[str, dict] = {}
    for _, r in s.iterrows():
        vid = r["video_id"]
        yt = r.get("youtube_id") if isinstance(r.get("youtube_id"), str) else vid.split("@")[0]
        kind = r.get("clip_source") if isinstance(r.get("clip_source"), str) else "short"
        start = r.get("window_start_s")
        end = r.get("window_end_s")
        start = None if start is None or pd.isna(start) else float(start)
        end = None if end is None or pd.isna(end) else float(end)
        out[vid] = {
            "youtube_id": yt,
            "kind": kind,
            "start_s": start,
            "end_s": end,
            "youtube_url": youtube_url(yt, kind, start),
        }
    return out
