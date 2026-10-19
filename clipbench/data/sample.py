"""data/sample.parquet: a stratified sample per language, Shorts first, then windows.

Eligible: `clip_source` "short" with 5 <= duration_s <= 90, or "window"; and a creator track, except
on platforms with no caption tracks (tiktok, instagram), whose clips are reviewed from scratch.

Caps and balance (docs/V2.md diversity rules):

- no channel above 5% of the target size: cap = floor(0.05 * n), at least 1, Shorts and windows counted
  together;
- categories balanced: clips are taken one category at a time in a seeded rotation (a clip's category
  is its clips.csv category, else the guess; clips with none form their own bucket); inside a category
  channels are visited round robin in a seeded order;
- Shorts first; windows only fill what Shorts could not (`shorts_only` skips them).

The file holds every language: writing one language replaces only its rows. `sample_rank` is the pick
order inside a language. If the pool cannot reach n under the caps, every clip the caps allow is taken
and the result says so.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.data.datasets import public_url
from clipbench.data.manifest import COLUMNS, load_manifest

MIN_DURATION_S = 5.0
MAX_DURATION_S = 90.0
MAX_CHANNEL_SHARE = 0.05
NO_TRACK_PLATFORMS = ("tiktok", "instagram")


def channel_cap(n: int) -> int:
    return max(1, math.floor(MAX_CHANNEL_SHARE * n))


def _source(m: pd.DataFrame) -> pd.Series:
    if "clip_source" in m:
        return m["clip_source"].fillna("short")
    return pd.Series("short", index=m.index)


def eligible(manifest: pd.DataFrame, *, shorts_only: bool = False) -> pd.DataFrame:
    m = manifest
    creator = m["has_creator_track"].fillna(False).astype(bool)
    if "platform" in m:
        creator = creator | m["platform"].isin(NO_TRACK_PLATFORMS)
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


def _category_key(row: pd.Series) -> str:
    v = row.get("category")
    return v if isinstance(v, str) and v else ""


def _pick(pool: pd.DataFrame, rng: random.Random, n_left: int, taken: dict[str, int], cap: int) -> list[int]:
    """Category rotation outside, channel round robin inside, channel cap shared via `taken`."""
    queues: dict[str, list[int]] = {}
    for cat in sorted(pool["_category"].unique()):
        sub = pool[pool["_category"] == cat]
        by_channel: dict[str, list[int]] = {}
        for ch in sorted(sub["_channel"].unique()):
            idx = sorted(sub.index[sub["_channel"] == ch], key=lambda i: pool.at[i, "video_id"])
            rng.shuffle(idx)
            by_channel[ch] = idx
        order = sorted(by_channel)
        rng.shuffle(order)
        q: list[int] = []
        depth = 0
        while True:
            added = False
            for ch in order:
                if depth < len(by_channel[ch]):
                    q.append(by_channel[ch][depth])
                    added = True
            if not added:
                break
            depth += 1
        queues[cat] = q
    cats = sorted(queues)
    rng.shuffle(cats)
    picked: list[int] = []
    pos = dict.fromkeys(cats, 0)
    while len(picked) < n_left:
        added = False
        for cat in cats:
            if len(picked) >= n_left:
                break
            q = queues[cat]
            while pos[cat] < len(q):
                i = q[pos[cat]]
                pos[cat] += 1
                ch = pool.at[i, "_channel"]
                if taken.get(ch, 0) < cap:
                    taken[ch] = taken.get(ch, 0) + 1
                    picked.append(i)
                    added = True
                    break
        if not added:
            break
    return picked


def stratified_sample(manifest: pd.DataFrame, n: int, seed: int, *, shorts_only: bool = False) -> pd.DataFrame:
    pool = eligible(manifest, shorts_only=shorts_only).copy()
    cols = [c for c in COLUMNS if c in pool.columns]
    if pool.empty:
        out = pool[cols].copy()
        out["sample_rank"] = pd.Series(dtype="int64")
        return out
    pool["_channel"] = pool.apply(_channel_key, axis=1)
    pool["_category"] = pool.apply(_category_key, axis=1)
    src = _source(pool)
    rng = random.Random(seed)
    cap = channel_cap(n)
    taken: dict[str, int] = {}
    picked = _pick(pool[src == "short"], rng, n, taken, cap)
    if not shorts_only and len(picked) < n:
        picked += _pick(pool[src == "window"], rng, n - len(picked), taken, cap)
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
    full = df
    if path.exists():
        old = pd.read_parquet(path)
        if "language" in old:
            old = old[old["language"] != language]
            if len(old):
                full = pd.concat([old.reindex(columns=list(df.columns)), df], ignore_index=True)
    full.to_parquet(path, index=False)

    chan = df["channel_id"].fillna(df["channel"]) if len(df) else pd.Series(dtype=str)
    src_df, src_pool = _source(df), _source(pool)
    cats = Counter(df["category"].fillna("(none)")) if len(df) else Counter()
    lines = [
        f"wrote {path} [{language}]: {len(df)} clips ({int((src_df == 'short').sum())} Shorts, "
        f"{int((src_df == 'window').sum())} windows) from {chan.nunique()} channels "
        f"(seed {seed}, cap {channel_cap(n)} per channel, largest channel {chan.value_counts().max() if len(df) else 0})",
        "  categories: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items())),
        f"  eligible pool: {int((src_pool == 'short').sum())} Shorts, {int((src_pool == 'window').sum())} windows, "
        f"{pool['channel_id'].fillna(pool['channel']).nunique() if len(pool) else 0} channels",
    ]
    if len(df) < n:
        lines.append(
            f"SHORT: asked for {n} but only {len(df)} clips are available under the per channel cap. "
            f"The sample is smaller than requested."
        )
    return SampleResult(df=df, path=path, n_target=n, n_eligible=len(pool), message="\n".join(lines))


def load_sample(settings: Settings | None = None, language: str | None = None) -> pd.DataFrame:
    settings = settings or get_settings()
    if not settings.sample_path.exists():
        raise FileNotFoundError(f"{settings.sample_path} not found; run `clipbench data sample` first")
    df = pd.read_parquet(settings.sample_path)
    if language and "language" in df:
        df = df[df["language"] == language]
    keys = [c for c in ("language", "sample_rank") if c in df]
    return df.sort_values(keys, kind="stable").reset_index(drop=True)


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
    missing = fresh["language"].isna()
    for c in cols:
        if c in sample:
            fresh.loc[missing, c] = sample.loc[missing, c]
    fresh = fresh[[c for c in COLUMNS if c in fresh.columns] + ["sample_rank"]]
    fresh.to_parquet(settings.sample_path, index=False)
    return fresh


def youtube_url(youtube_id: str, clip_source: str | None = None, start_s: float | None = None) -> str:
    """Shorts link for a Short; watch link with `t=<start>s` for a window."""
    start_ms = None
    if clip_source == "window" and start_s is not None and not pd.isna(start_s):
        start_ms = int(start_s * 1000)
    return public_url("youtube", youtube_id, start_ms)


def _f(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def clip_meta(settings: Settings | None = None) -> dict[str, dict]:
    """Per clip key (`video_id`): platform, source_id, youtube_id, kind ("short"|"window"), start_s,
    end_s, category, category_guess, url (public link, `t=` for windows) and youtube_url (same, kept
    for older callers). Read from data/sample.parquet, for the review UI, runner, scoring and export."""
    settings = settings or get_settings()
    if not settings.sample_path.exists():
        return {}
    s = pd.read_parquet(settings.sample_path)
    out: dict[str, dict] = {}
    for _, r in s.iterrows():
        vid = r["video_id"]
        platform = r.get("platform") if isinstance(r.get("platform"), str) else "youtube"
        source_id = r.get("source_id") if isinstance(r.get("source_id"), str) else None
        source_id = source_id or (r.get("youtube_id") if isinstance(r.get("youtube_id"), str) else vid.split("@")[0])
        kind = r.get("clip_source") if isinstance(r.get("clip_source"), str) else "short"
        start, end = _f(r.get("window_start_s")), _f(r.get("window_end_s"))
        url = public_url(platform, source_id, int(start * 1000) if kind == "window" and start is not None else None)
        cat = r.get("category") if isinstance(r.get("category"), str) else None
        out[vid] = {
            "platform": platform,
            "source_id": source_id,
            "youtube_id": source_id if platform == "youtube" else None,
            "kind": kind,
            "start_s": start,
            "end_s": end,
            "category": cat,
            "category_guess": bool(r.get("category_guess")) if cat else False,
            "url": url,
            "youtube_url": url,
        }
    return out
