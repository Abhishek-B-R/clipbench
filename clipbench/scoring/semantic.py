"""Semantic WER (A2): aggregates of the LLM judge's meaning scores and the publishing gate.

The judge itself lives in clipbench/judge (pinned prompt, Workers AI, cache, budget). This module reads
results/<run_id>/semantic.parquet, aggregates it, and decides whether the scores may be published: only
for a language whose judge calibration against human labels passes (>= 50 pairs, Spearman >= 0.7,
mean absolute error <= 0.2, for the same judge model and prompt version).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings
from clipbench.scoring.quality_io import frame_records, grouped, rate_ci


def _metrics(seed: int):
    def fn(g: pd.DataFrame) -> dict[str, Any]:
        ok = g[g["semantic_score"].notna()]
        mean = rate_ci(ok["semantic_score"], np.ones(len(ok)), seed)
        return {
            "n_scored": int(len(ok)),
            "n_failed": int(g["judge_failed"].sum()) if "judge_failed" in g else 0,
            "semantic_score": mean[0], "semantic_ci_low": mean[1], "semantic_ci_high": mean[2],
            "semantic_error": 1 - mean[0] if not np.isnan(mean[0]) else np.nan,
            "share_full_meaning": float((ok["semantic_score"] >= 0.999).mean()) if len(ok) else np.nan,
        }  # fmt: skip

    return fn


def semantic_aggregates(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Mean judge score (meaning kept, 0 to 1) per provider and slice, with a clip bootstrap CI."""
    if df is None or df.empty:
        return pd.DataFrame()
    return grouped(df, _metrics(seed))


def publish_status(calibration: dict[str, Any] | None, language: str, judge_model: str, prompt_version: str) -> tuple[bool, str]:
    """(may publish, why). Refused without a passing calibration for this judge, prompt and language."""
    if not calibration:
        return False, "no judge calibration yet: run clipbench judge label and clipbench judge calibrate"
    if calibration.get("judge_model") != judge_model or calibration.get("prompt_version") != prompt_version:
        return False, (
            f"calibration is for {calibration.get('judge_model')} {calibration.get('prompt_version')}, "
            f"scores are from {judge_model} {prompt_version}"
        )
    stats = (calibration.get("languages") or {}).get(language)
    if not stats:
        return False, f"no calibration for {language}"
    if not stats.get("passes"):
        return False, f"calibration for {language} does not pass: {stats.get('reason')}"
    return True, f"calibrated on {stats.get('n_pairs')} human labelled pairs"


def export_payload(run_id: str, settings: Settings) -> dict[str, Any] | None:
    from clipbench.judge.run import load_calibration

    path = settings.results_dir / run_id / "semantic.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    language = str(df["language"].iloc[0]).split("-")[0]
    model, version = str(df["judge_model"].iloc[0]), str(df["prompt_version"].iloc[0])
    cal = load_calibration(settings, run_id)
    ok, reason = publish_status(cal, language, model, version)
    agg_path = path.with_name("semantic_aggregates.parquet")
    agg = pd.read_parquet(agg_path) if agg_path.exists() else semantic_aggregates(df, settings.sample_seed)
    stats = ((cal or {}).get("languages") or {}).get(language)
    return {
        "judge_model": model,
        "prompt_version": version,
        "language": language,
        "published": ok,
        "reason": reason,
        "calibration": None if stats is None else {
            k: stats.get(k) for k in ("n_pairs", "n_labellers", "spearman", "mae", "passes")
        } | {"thresholds": (cal or {}).get("thresholds")},
        "n_pairs_scored": int(df["semantic_score"].notna().sum()),
        "aggregates": (frame_records(agg) or []) if ok else [],
    }
