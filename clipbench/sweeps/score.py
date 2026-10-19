"""`clipbench sweep score <sweep_id>`: how fast each provider falls apart as the bed gets louder.

Per clip scoring reuses clipbench.scoring.score (same normalisers, alignment and hallucination rules as a
normal run). Then per provider, metric and condition (clean, or bed kind x SNR, dry or with reverb):

- `error_rate` with a 95% clip bootstrap CI (corpus rate), and `rel_to_clean` = rate / dry clean rate;
- hallucination over non-speech: words per non-speech minute with a bootstrap CI (the pads around every
  clip are known non-speech), plus the v1 `hallucination_rate` and `long_output_rate`;
- `doubling_snr_db`: the SNR at which the error rate reaches twice the dry clean rate (clean rate floored at
  CLEAN_FLOOR), linearly interpolated between the two SNR levels around the crossing. Higher means the
  provider falls apart earlier. `doubling_censored` is "above" when the rate is already doubled at the
  highest SNR tested (the value is then that SNR, a lower bound) and "below" when it never doubles down to
  the lowest SNR tested (value null). The CI comes from the same clip resamples applied to every condition.

Outputs in results/sweeps/<sweep_id>/: sweep_scores.parquet (per variant, numbers only) and sweeps.parquet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.scoring.stats import N_RESAMPLES, bootstrap_ci, corpus_rate
from clipbench.sweeps.build import load_meta, sweep_data_dir, sweep_results_dir

CLEAN_FLOOR = 0.01
CLEAN = "clean"

SWEEP_SCORE_COLUMNS = [
    "sweep_id", "provider", "model_id", "video_id", "clip_id", "tier", "language", "metric", "is_primary",
    "condition", "bed_kind", "bed_id", "snr_db", "reverb", "ref_len", "errors", "error_rate",
    "hallucinated_tokens", "long_output", "nonspeech_minutes", "halluc_nonspeech_words",
    "halluc_per_nonspeech_min",
]  # fmt: skip
SWEEPS_COLUMNS = [
    "sweep_id", "provider", "model_id", "tier", "language", "metric", "is_primary", "slice_kind", "slice",
    "condition", "bed_kind", "snr_db", "reverb", "n_clips", "error_rate", "ci_low", "ci_high",
    "clean_error_rate", "rel_to_clean", "halluc_n_clips", "nonspeech_minutes", "halluc_nonspeech_words",
    "halluc_per_nonspeech_min", "halluc_ci_low", "halluc_ci_high", "hallucination_rate", "long_output_rate",
    "doubling_snr_db", "doubling_ci_low", "doubling_ci_high", "doubling_censored",
]  # fmt: skip


def doubling_snr(
    levels: list[tuple[float, float]], clean_rate: float, *, floor: float = CLEAN_FLOOR
) -> tuple[float | None, str | None]:
    """(snr, censored) where the rate first reaches 2 x max(clean_rate, floor), walking from high to low SNR."""
    pts = sorted(((float(s), float(r)) for s, r in levels if not np.isnan(r)), key=lambda p: -p[0])
    if not pts or np.isnan(clean_rate):
        return None, None
    thr = 2 * max(float(clean_rate), floor)
    for i, (snr, rate) in enumerate(pts):
        if rate >= thr:
            if i == 0:
                return snr, "above"
            s0, r0 = pts[i - 1]
            if rate == r0:
                return snr, None
            return float(s0 + (thr - r0) * (snr - s0) / (rate - r0)), None
    return None, "below"


def _doubling_ci(
    errors: np.ndarray, ref_lens: np.ndarray, snrs: list[float], seed: int, n_resamples: int = N_RESAMPLES
) -> tuple[float | None, float | None]:
    """errors: (n_clips, 1 + n_levels), column 0 the dry clean condition. Paired clip resampling."""
    n = errors.shape[0]
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    den = ref_lens[idx].sum(axis=1).astype(float)
    num = errors[idx].sum(axis=1)  # (n_resamples, n_cols)
    rates = np.divide(num, den[:, None], out=np.zeros_like(num, dtype=float), where=den[:, None] > 0)
    values = []
    for row in rates:
        snr, censored = doubling_snr(list(zip(snrs, row[1:], strict=True)), row[0])
        values.append(-np.inf if censored == "below" else (np.nan if snr is None else snr))
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if not len(arr):
        return None, None
    low, high = (float(x) for x in np.quantile(arr, [0.025, 0.975], method="nearest"))
    return (None if np.isinf(low) else low), (None if np.isinf(high) else high)


def _per_clip(sweep_id: str, settings: Settings) -> pd.DataFrame:
    from clipbench.scoring.score import _RefCache, _score_rows  # the run scorer, so the rules are identical

    rows_path = sweep_results_dir(sweep_id, settings) / "rows.parquet"
    if not rows_path.exists():
        raise FileNotFoundError(f"no rows for sweep {sweep_id}; run `clipbench sweep run {sweep_id}` first")
    rows = pd.read_parquet(rows_path)
    data = sweep_data_dir(sweep_id, settings)
    texts = pd.read_parquet(data / "texts.parquet")
    refs = _RefCache(pd.read_parquet(data / "refs.parquet"))
    recipes = pd.read_parquet(sweep_results_dir(sweep_id, settings) / "recipes.parquet")
    scores = _score_rows(rows, texts, refs)
    keep = recipes[["variant_id", "clip_id", "condition", "bed_kind", "bed_id", "snr_db", "reverb"]]
    merged = scores.merge(keep, left_on="video_id", right_on="variant_id", how="inner")
    merged["sweep_id"] = sweep_id
    merged["bed_kind"] = merged["bed_kind"].fillna("none")
    return merged.reindex(columns=SWEEP_SCORE_COLUMNS)


def _halluc(group: pd.DataFrame, seed: int) -> dict[str, Any]:
    ns = group[group["nonspeech_minutes"].fillna(0).gt(0) & group["halluc_nonspeech_words"].notna()]
    if not len(ns):
        return {"halluc_n_clips": 0, "nonspeech_minutes": np.nan, "halluc_nonspeech_words": np.nan,
                "halluc_per_nonspeech_min": np.nan, "halluc_ci_low": np.nan, "halluc_ci_high": np.nan}  # fmt: skip
    ci = bootstrap_ci(ns["halluc_nonspeech_words"].to_numpy(float), ns["nonspeech_minutes"].to_numpy(float), seed=seed)
    return {
        "halluc_n_clips": int(len(ns)),
        "nonspeech_minutes": float(ns["nonspeech_minutes"].sum()),
        "halluc_nonspeech_words": float(ns["halluc_nonspeech_words"].sum()),
        "halluc_per_nonspeech_min": ci.rate,
        "halluc_ci_low": ci.ci_low,
        "halluc_ci_high": ci.ci_high,
    }


def aggregate(scores: pd.DataFrame, sweep_id: str, seed: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    if scores.empty:
        return pd.DataFrame(columns=SWEEPS_COLUMNS)
    for (provider, metric), part in scores.groupby(["provider", "metric"], sort=True):
        clean = part[part["condition"] == CLEAN]
        clean_rate = corpus_rate(clean["errors"].to_numpy(float), clean["ref_len"].to_numpy(float)) if len(clean) else np.nan
        # doubling per series (bed kind, reverb), paired over the clips that have every condition
        doubling: dict[tuple[str, bool], dict[str, Any]] = {}
        for (kind, rev), series in part[part["bed_kind"] != "none"].groupby(["bed_kind", "reverb"], sort=True):
            snrs = sorted(series["snr_db"].dropna().unique(), reverse=True)
            levels = []
            for snr in snrs:
                g = series[series["snr_db"] == snr]
                levels.append((snr, corpus_rate(g["errors"].to_numpy(float), g["ref_len"].to_numpy(float))))
            value, censored = doubling_snr(levels, clean_rate)
            pivot = pd.concat([clean.assign(snr_db=np.inf), series]).pivot_table(
                index="clip_id", columns="snr_db", values="errors", aggfunc="first"
            )
            cols = [np.inf, *snrs]
            pivot = pivot.reindex(columns=cols).dropna()
            ref_lens = clean.drop_duplicates("clip_id").set_index("clip_id")["ref_len"].reindex(pivot.index)
            low, high = _doubling_ci(pivot.to_numpy(float), ref_lens.to_numpy(float), [float(s) for s in snrs], seed)
            doubling[(kind, bool(rev))] = {"doubling_snr_db": value, "doubling_censored": censored,
                                            "doubling_ci_low": low, "doubling_ci_high": high}  # fmt: skip
        for cond, group in part.groupby("condition", sort=True):
            first = group.iloc[0]
            ci = bootstrap_ci(group["errors"].to_numpy(float), group["ref_len"].to_numpy(float), seed=seed)
            kind = str(first["bed_kind"])
            rev = bool(first["reverb"])
            ref_total = group["ref_len"].sum()
            records.append(
                {
                    "sweep_id": sweep_id,
                    "provider": provider,
                    "model_id": ",".join(sorted({str(m) for m in group["model_id"].dropna()})),
                    "tier": str(first["tier"]),
                    "language": str(first["language"]),
                    "metric": metric,
                    "is_primary": bool(first["is_primary"]),
                    "slice_kind": "sweep_condition",
                    "slice": cond,
                    "condition": cond,
                    "bed_kind": kind,
                    "snr_db": first["snr_db"],
                    "reverb": rev,
                    "n_clips": int(len(group)),
                    "error_rate": ci.rate,
                    "ci_low": ci.ci_low,
                    "ci_high": ci.ci_high,
                    "clean_error_rate": clean_rate,
                    "rel_to_clean": ci.rate / clean_rate if clean_rate and not np.isnan(clean_rate) else np.nan,
                    **_halluc(group, seed),
                    "hallucination_rate": float(group["hallucinated_tokens"].sum() / max(ref_total, 1)),
                    "long_output_rate": float(group["long_output"].astype(float).mean()),
                    **doubling.get((kind, rev), {"doubling_snr_db": None, "doubling_censored": None,
                                                 "doubling_ci_low": None, "doubling_ci_high": None}),  # fmt: skip
                }
            )
    df = pd.DataFrame(records, columns=SWEEPS_COLUMNS)
    for col in ("snr_db", "doubling_snr_db", "doubling_ci_low", "doubling_ci_high"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["doubling_censored"] = df["doubling_censored"].astype("string")
    order = {"none": 0, "music": 1, "noise": 2}
    df = df.assign(_k=df["bed_kind"].map(order).fillna(9), _s=-df["snr_db"].fillna(np.inf))
    return df.sort_values(["provider", "metric", "reverb", "_k", "_s"]).drop(columns=["_k", "_s"]).reset_index(drop=True)


def score_sweep(sweep_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    scores = _per_clip(sweep_id, s)
    out = sweep_results_dir(sweep_id, s)
    scores.to_parquet(out / "sweep_scores.parquet", index=False)
    agg = aggregate(scores, sweep_id, s.sample_seed)
    meta = load_meta(sweep_id, s)
    if meta.get("language") and len(agg):
        agg["language"] = meta["language"]
    agg.to_parquet(out / "sweeps.parquet", index=False)
    return out / "sweeps.parquet"
