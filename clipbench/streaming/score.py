"""`clipbench score-stream <run_id>`: aggregates of a streaming run, with 95% bootstrap intervals.

Reads results/runs/<run_id>/stream_rows.parquet. When the run's texts and reference snapshot are on disk
(data/runs/<run_id>/), every per clip metric is recomputed from the recorded events first, so a change in
normalisation applies to old runs too. Writes results/<run_id>/stream_aggregates.parquet:

    provider, model_id, tier, language, slice_kind ("all" | "condition" | "category"), slice, n_clips,
    too_small, metric, error_rate, ci_low, ci_high,
    partial_rewrite_rate, rewrite_ci_low, rewrite_ci_high,
    revisions_per_word, revisions_ci_low, revisions_ci_high,
    ttfp_p50_ms, ttfp_p90_ms, ttfp_ci_low, ttfp_ci_high, ttfs_p50_ms, ttfs_p90_ms, ttfs_ci_low, ttfs_ci_high,
    timing_n_words, timing_median_ms, timing_within_200ms, cost_usd_total, audio_minutes, usd_per_minute,
    timed_out_rate, speed, chunk_ms

Rates are corpus rates with the clip as the bootstrap unit (`clipbench.scoring.stats.bootstrap_ci`): error rate
= errors / reference tokens, rewrite rate = rewritten / partial tokens, revisions = revisions / final tokens.
Latencies are medians over clips with a percentile bootstrap of the median (same resample count and seed).
Condition slices: every condition in `CONDITIONS` present, plus `no_music` for clips without music.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import CATEGORIES, CONDITIONS, Word
from clipbench.scoring.stats import ALPHA, DEFAULT_SEED, N_RESAMPLES, bootstrap_ci
from clipbench.streaming.base import StreamTranscript
from clipbench.streaming.metrics import clip_metrics
from clipbench.streaming.runner import METRIC_COLUMNS, rows_frame

TOO_SMALL = 15

AGGREGATE_COLUMNS = [
    "provider", "model_id", "tier", "language", "slice_kind", "slice", "n_clips", "too_small", "metric",
    "error_rate", "ci_low", "ci_high", "partial_rewrite_rate", "rewrite_ci_low", "rewrite_ci_high",
    "revisions_per_word", "revisions_ci_low", "revisions_ci_high", "ttfp_p50_ms", "ttfp_p90_ms", "ttfp_ci_low",
    "ttfp_ci_high", "ttfs_p50_ms", "ttfs_p90_ms", "ttfs_ci_low", "ttfs_ci_high", "timing_n_words",
    "timing_median_ms", "timing_within_200ms", "cost_usd_total", "audio_minutes", "usd_per_minute",
    "timed_out_rate", "speed", "chunk_ms",
]  # fmt: skip


def median_ci(values, *, n_resamples: int = N_RESAMPLES, seed: int = DEFAULT_SEED, alpha: float = ALPHA):
    """(median, p90, ci_low, ci_high) of the clip values, NaN when there are none."""
    v = np.asarray([x for x in values if x is not None and not pd.isna(x)], dtype=float)
    if len(v) == 0:
        nan = float("nan")
        return nan, nan, nan, nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(n_resamples, len(v)))
    meds = np.median(v[idx], axis=1)
    low, high = np.quantile(meds, [alpha / 2, 1 - alpha / 2])
    return float(np.median(v)), float(np.percentile(v, 90)), float(low), float(high)


def _rate(num: pd.Series, den: pd.Series) -> tuple[float, float, float]:
    num = pd.to_numeric(num, errors="coerce").fillna(0).astype(float)
    den = pd.to_numeric(den, errors="coerce").fillna(0).astype(float)
    if len(den) == 0 or float(den.sum()) <= 0:
        nan = float("nan")
        return nan, nan, nan
    ci = bootstrap_ci(num.to_numpy(), den.to_numpy())
    return ci.rate, ci.ci_low, ci.ci_high


def _conditions(value) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    try:
        out = json.loads(str(value))
    except ValueError:
        return []
    return [str(x) for x in out] if isinstance(out, list) else []


def slices(rows: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    out = [("all", "all", pd.Series(True, index=rows.index))]
    conds = rows["conditions"].map(_conditions)
    for cond in CONDITIONS:
        mask = conds.map(lambda c, k=cond: k in c)
        if mask.any():
            out.append(("condition", cond, mask))
    mask = conds.map(lambda c: "music" not in c)
    if mask.any() and not mask.all():
        out.append(("condition", "no_music", mask))
    for cat in CATEGORIES:
        mask = rows["category"] == cat
        if mask.fillna(False).any():
            out.append(("category", cat, mask.fillna(False)))
    return out


def aggregate(rows: pd.DataFrame) -> pd.DataFrame:
    ok = rows[rows["status"] == "ok"]
    records = []
    for (provider, tier, language), group in ok.groupby(["provider", "tier", "language"], dropna=False):
        for kind, name, mask in slices(group):
            g = group[mask]
            if g.empty:
                continue
            err, lo, hi = _rate(g["errors"], g["ref_len"])
            rw, rw_lo, rw_hi = _rate(g["rewritten_tokens"], g["partial_tokens"])
            rv, rv_lo, rv_hi = _rate(g["revisions"], g["final_tokens"])
            fp50, fp90, fplo, fphi = median_ci(g["ttfp_ms"])
            fs50, fs90, fslo, fshi = median_ci(g["ttfs_ms"])
            tn = pd.to_numeric(g["timing_n_words"], errors="coerce").fillna(0)
            within = pd.to_numeric(g["timing_within_200ms"], errors="coerce")
            timing_n = int(tn.sum())
            timing_within = float((within.fillna(0) * tn).sum() / timing_n) if timing_n else float("nan")
            timing_med = pd.to_numeric(g["timing_median_ms"], errors="coerce").dropna()
            cost = float(pd.to_numeric(g["cost_usd"], errors="coerce").fillna(0).sum())
            minutes = float(pd.to_numeric(g["audio_seconds"], errors="coerce").fillna(0).sum()) / 60.0
            records.append(
                {
                    "provider": provider,
                    "model_id": str(g["model_id"].mode().iloc[0]) if g["model_id"].notna().any() else None,
                    "tier": tier, "language": language, "slice_kind": kind, "slice": name,
                    "n_clips": int(g["video_id"].nunique()), "too_small": bool(g["video_id"].nunique() < TOO_SMALL),
                    "metric": str(g["metric"].dropna().iloc[0]) if g["metric"].notna().any() else None,
                    "error_rate": err, "ci_low": lo, "ci_high": hi,
                    "partial_rewrite_rate": rw, "rewrite_ci_low": rw_lo, "rewrite_ci_high": rw_hi,
                    "revisions_per_word": rv, "revisions_ci_low": rv_lo, "revisions_ci_high": rv_hi,
                    "ttfp_p50_ms": fp50, "ttfp_p90_ms": fp90, "ttfp_ci_low": fplo, "ttfp_ci_high": fphi,
                    "ttfs_p50_ms": fs50, "ttfs_p90_ms": fs90, "ttfs_ci_low": fslo, "ttfs_ci_high": fshi,
                    "timing_n_words": timing_n,
                    "timing_median_ms": float(timing_med.median()) if len(timing_med) else float("nan"),
                    "timing_within_200ms": timing_within,
                    "cost_usd_total": cost, "audio_minutes": minutes,
                    "usd_per_minute": cost / minutes if minutes > 0 else float("nan"),
                    "timed_out_rate": float(g["timed_out"].astype(bool).mean()),
                    "speed": float(pd.to_numeric(g["speed"], errors="coerce").max()),
                    "chunk_ms": int(pd.to_numeric(g["chunk_ms"], errors="coerce").max()),
                }  # fmt: skip
            )
    df = pd.DataFrame(records, columns=AGGREGATE_COLUMNS)
    df["n_clips"] = df["n_clips"].astype("int64")
    df["too_small"] = df["too_small"].astype(bool)
    return df


def rescore(rows: pd.DataFrame, texts_path: Path, refs_path: Path) -> pd.DataFrame:
    """Recompute every per clip metric from the recorded events and the reference snapshot."""
    if not texts_path.exists() or not refs_path.exists():
        return rows
    texts = pd.read_parquet(texts_path)
    refs = pd.read_parquet(refs_path).set_index("video_id")
    tx = {(r.provider, r.video_id): r.transcript_json for r in texts.itertuples(index=False)}
    out = rows.astype(object).where(rows.notna(), None).to_dict("records")
    for row in out:
        key = (row["provider"], row["video_id"])
        if row.get("status") != "ok" or key not in tx or row["video_id"] not in refs.index or tx[key] is None:
            continue
        ref = refs.loc[row["video_id"]]
        words = None
        if ref.get("words_json") is not None and not pd.isna(ref.get("words_json")):
            words = [Word(**w) for w in json.loads(ref["words_json"])]
        t = StreamTranscript.model_validate_json(tx[key])
        m = clip_metrics(t, str(ref["ref_text"] or ""), words, str(row["language"]))
        row.update({k: v for k, v in m.as_dict().items() if k in METRIC_COLUMNS})
    return rows_frame(out)


def stream_rows_path(run_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.runs_dir / run_id / "stream_rows.parquet"


def resolve_stream_run_id(run_id: str, settings: Settings | None = None) -> str:
    """'latest' means the most recently modified streaming run."""
    if run_id != "latest":
        return run_id
    s = settings or get_settings()
    runs = [p for p in s.runs_dir.glob("*") if (p / "stream_rows.parquet").exists()]
    if not runs:
        raise FileNotFoundError(f"no streaming runs under {s.runs_dir}")
    return max(runs, key=lambda p: p.stat().st_mtime).name


def score_stream(run_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    path = stream_rows_path(run_id, s)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; is {run_id} a streaming run?")
    rows = pd.read_parquet(path)
    text_dir = s.runs_text_dir / run_id
    rows = rescore(rows, text_dir / "stream_texts.parquet", text_dir / "refs.parquet")
    out_dir = s.results_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    agg = aggregate(rows)
    target = out_dir / "stream_aggregates.parquet"
    tmp = target.with_name(target.name + ".tmp")
    agg.to_parquet(tmp, index=False)
    tmp.replace(target)
    return target
