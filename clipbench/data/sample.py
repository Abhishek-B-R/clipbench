"""data/sample.parquet: a stratified sample of creator-captioned short clips.

Eligible: a creator track and 5 <= duration_s <= 90. No channel may be more than 10% of the
target size (cap = floor(0.10 * n), at least 1). Channels are visited round robin in a seeded
order, one clip each per round, so the set spreads over as many channels as possible. If the
eligible pool cannot reach n under the cap, every clip the cap allows is taken and the result
says so.
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


def eligible(manifest: pd.DataFrame) -> pd.DataFrame:
    m = manifest
    mask = m["has_creator_track"].fillna(False).astype(bool) & m["duration_s"].between(MIN_DURATION_S, MAX_DURATION_S)
    return m[mask]


def _channel_key(row: pd.Series) -> str:
    for col in ("channel_id", "channel"):
        v = row.get(col)
        if isinstance(v, str) and v:
            return v
    return "<no channel>"


def stratified_sample(manifest: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    pool = eligible(manifest).copy()
    cols = [c for c in COLUMNS if c in pool.columns]
    if pool.empty:
        out = pool[cols].copy()
        out["sample_rank"] = pd.Series(dtype="int64")
        return out
    pool["_channel"] = pool.apply(_channel_key, axis=1)
    rng = random.Random(seed)
    cap = channel_cap(n)

    by_channel: dict[str, list[int]] = {}
    for ch in sorted(pool["_channel"].unique()):
        idx = sorted(pool.index[pool["_channel"] == ch], key=lambda i: pool.at[i, "video_id"])
        rng.shuffle(idx)
        by_channel[ch] = idx[:cap]
    order = sorted(by_channel)
    rng.shuffle(order)

    picked: list[int] = []
    depth = 0
    while len(picked) < n:
        added = False
        for ch in order:
            if depth < len(by_channel[ch]) and len(picked) < n:
                picked.append(by_channel[ch][depth])
                added = True
        if not added:
            break
        depth += 1

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
    n: int = 150, seed: int | None = None, language: str | None = None, settings: Settings | None = None
) -> SampleResult:
    settings = settings or get_settings()
    seed = settings.sample_seed if seed is None else seed
    language = language or settings.language
    manifest = load_manifest(settings)
    manifest = manifest[manifest["language"] == language]
    pool = eligible(manifest)
    df = stratified_sample(manifest, n, seed)
    path = settings.sample_path
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)

    channels = df["channel_id"].fillna(df["channel"]).nunique() if len(df) else 0
    top = df["channel_id"].fillna(df["channel"]).value_counts().max() if len(df) else 0
    lines = [
        f"wrote {path}: {len(df)} clips from {channels} channels (seed {seed}, cap {channel_cap(n)} per channel, "
        f"largest channel {top})",
        f"eligible pool: {len(pool)} clips with a creator track and {MIN_DURATION_S:.0f}-{MAX_DURATION_S:.0f} s, "
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
