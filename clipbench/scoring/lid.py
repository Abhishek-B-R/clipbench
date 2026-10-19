"""B16 language identification.

On runs made with `--detect-language` (row option detect_language), each provider got no language
hint and reported `detected_language`. Two questions:

1. LID accuracy: share of clips where the detected language (normalised to ISO-639-1, so "ja-JP",
   "jpn" and "japanese" all count as "ja") equals the clip's language. A provider that reported no
   language on any clip of the run does not offer detection and is left out; a missing answer from a
   provider that does report one counts as wrong.
2. The cost of no hint: the primary error rate on the detect run versus a hinted run of the same
   provider on the same clips (paired bootstrap over the common clips). The hinted run is the most
   recent other run in the same language, without detect_language, that has scores for that provider.

Writes results/<run_id>/lid.parquet and lid_aggregates.parquet. Numbers and language codes only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.providers.base import normalize_language_code
from clipbench.scoring.stats import bootstrap_ci, paired_bootstrap
from clipbench.scoring.v3_inputs import TOO_SMALL, load_run, missing, model_ids, records, row_options, slices

CLIP_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "conditions", "detected_language", "correct",
    "errors", "ref_len", "error_rate", "hinted_run_id", "hinted_errors", "hinted_ref_len", "hinted_error_rate",
]
AGG_COLUMNS = [
    "provider", "model_id", "tier", "language", "slice_kind", "slice", "n_clips", "too_small",
    "lid_accuracy", "lid_ci_low", "lid_ci_high", "n_unreported",
    "error_rate_detect", "error_rate_detect_ci_low", "error_rate_detect_ci_high",
    "n_paired", "error_rate_hinted", "error_rate_diff", "diff_ci_low", "diff_ci_high", "verdict",
    "wrong_language_error_rate",
]


def _primary_scores(settings: Settings, run_id: str) -> pd.DataFrame | None:
    path = settings.results_dir / run_id / "scores.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if "is_primary" in df.columns:
        df = df[df["is_primary"].astype(bool)]
    return df


def _is_detect_run(rows: pd.DataFrame) -> pd.Series:
    return rows["options"].map(lambda v: bool(row_options(v).get("detect_language")))


def find_hinted_runs(run_id: str, language: str, providers: set[str], settings: Settings) -> dict[str, str]:
    """provider -> most recent other run in `language` without detect_language that has scores for it."""
    out: dict[str, str] = {}
    runs_dir = settings.runs_dir
    if not runs_dir.exists():
        return out
    for other in sorted((p.name for p in runs_dir.iterdir() if p.is_dir()), reverse=True):
        if other == run_id or not (runs_dir / other / "rows.parquet").exists():
            continue
        rows = pd.read_parquet(runs_dir / other / "rows.parquet")
        if rows.empty or "options" not in rows.columns:
            continue
        langs = {normalize_language_code(x) for x in rows["language"].dropna()}
        if langs != {language}:
            continue
        hinted = rows[~_is_detect_run(rows)]
        scores = _primary_scores(settings, other)
        if scores is None:
            continue
        for p in set(hinted["provider"]) & set(scores["provider"]) & providers:
            out.setdefault(str(p), other)
        if providers <= set(out):
            break
    return out


def score_frame(run_id: str, settings: Settings) -> pd.DataFrame:
    inputs = load_run(run_id, settings)
    if inputs is None or "options" not in inputs.rows.columns:
        return pd.DataFrame(columns=CLIP_COLUMNS)
    main = inputs.main_rows()
    main = main[_is_detect_run(main)]
    if main.empty:
        return pd.DataFrame(columns=CLIP_COLUMNS)
    reporting = {
        p for p, g in main.groupby("provider") if g["detected_language"].map(lambda v: not missing(v)).any()
    }
    main = main[main["provider"].isin(reporting)]
    if main.empty:
        return pd.DataFrame(columns=CLIP_COLUMNS)
    language = normalize_language_code(main["language"].iloc[0]) or ""
    scores = _primary_scores(settings, run_id)
    own = {} if scores is None else {(r.provider, r.video_id): r for r in scores.itertuples(index=False)}
    hinted_runs = find_hinted_runs(run_id, language, reporting, settings)
    hinted: dict[tuple[str, str], Any] = {}
    for provider, other in hinted_runs.items():
        hs = _primary_scores(settings, other)
        for r in hs[hs["provider"] == provider].itertuples(index=False):
            hinted[(provider, r.video_id)] = r
    out = []
    for row in main.itertuples(index=False):
        detected = None if missing(row.detected_language) else normalize_language_code(row.detected_language)
        ref = inputs.ref(row.video_id, row.tier)
        s = own.get((row.provider, row.video_id))
        h = hinted.get((row.provider, row.video_id))
        out.append(
            {
                "provider": row.provider,
                "model_id": row.model_id,
                "video_id": row.video_id,
                "tier": row.tier,
                "language": row.language,
                "conditions": json.dumps(ref.conditions if ref else []),
                "detected_language": detected,
                "correct": detected == normalize_language_code(row.language),
                "errors": float(s.errors) if s is not None else np.nan,
                "ref_len": float(s.ref_len) if s is not None else np.nan,
                "error_rate": float(s.error_rate) if s is not None else np.nan,
                "hinted_run_id": hinted_runs.get(row.provider) if h is not None else None,
                "hinted_errors": float(h.errors) if h is not None else np.nan,
                "hinted_ref_len": float(h.ref_len) if h is not None else np.nan,
                "hinted_error_rate": float(h.error_rate) if h is not None else np.nan,
            }
        )
    return pd.DataFrame(out, columns=CLIP_COLUMNS)


def _agg(g: pd.DataFrame, seed: int) -> dict[str, Any]:
    nan = float("nan")
    acc = bootstrap_ci(g["correct"].astype(float).to_numpy(), np.ones(len(g)), seed=seed)
    scored = g[g["ref_len"].notna()]
    det = bootstrap_ci(scored["errors"].to_numpy(), scored["ref_len"].to_numpy(), seed=seed)
    paired = scored[scored["hinted_ref_len"].notna()]
    rec: dict[str, Any] = {
        "lid_accuracy": acc.rate,
        "lid_ci_low": acc.ci_low,
        "lid_ci_high": acc.ci_high,
        "n_unreported": int(g["detected_language"].isna().sum()),
        "error_rate_detect": det.rate if len(scored) else nan,
        "error_rate_detect_ci_low": det.ci_low if len(scored) else nan,
        "error_rate_detect_ci_high": det.ci_high if len(scored) else nan,
        "n_paired": int(len(paired)),
        "error_rate_hinted": nan,
        "error_rate_diff": nan,
        "diff_ci_low": nan,
        "diff_ci_high": nan,
        "verdict": None,
        "wrong_language_error_rate": nan,
    }
    if len(paired):
        # Both rates over the hinted reference length; the references are the same clips.
        res = paired_bootstrap(
            paired["errors"].to_numpy(), paired["hinted_errors"].to_numpy(), paired["ref_len"].to_numpy(), seed=seed
        )
        rec.update(
            error_rate_hinted=float(paired["hinted_errors"].sum() / max(paired["ref_len"].sum(), 1)),
            error_rate_diff=res.diff,
            diff_ci_low=res.ci_low,
            diff_ci_high=res.ci_high,
            verdict={"a_better": "detect_better", "b_better": "hinted_better"}.get(res.verdict, "no_difference"),
        )
    wrong = scored[~scored["correct"].astype(bool)]
    if len(wrong) and wrong["ref_len"].sum() > 0:
        rec["wrong_language_error_rate"] = float(wrong["errors"].sum() / wrong["ref_len"].sum())
    return rec


def aggregate(clips: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    if clips.empty:
        return pd.DataFrame(columns=AGG_COLUMNS)
    recs = []
    for (tier, language), part in clips.groupby(["tier", "language"], sort=True):
        for kind, name, mask in slices(part):
            for provider, g in part[mask].groupby("provider", sort=True):
                recs.append(
                    {
                        "provider": provider,
                        "model_id": model_ids(g),
                        "tier": tier,
                        "language": language,
                        "slice_kind": kind,
                        "slice": name,
                        "n_clips": int(len(g)),
                        "too_small": bool(len(g) < TOO_SMALL),
                        **_agg(g, seed),
                    }
                )
    return pd.DataFrame(recs, columns=AGG_COLUMNS)


def score_lid(run_id: str, settings: Settings | None = None) -> Path | None:
    """Write lid.parquet and lid_aggregates.parquet; None when the run has no detect_language rows.
    Run after score_run so the run's scores.parquet exists (else error rates are left empty)."""
    s = settings or get_settings()
    clips = score_frame(run_id, s)
    if clips.empty:
        return None
    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    clips.to_parquet(out / "lid.parquet", index=False)
    aggregate(clips, s.sample_seed).to_parquet(out / "lid_aggregates.parquet", index=False)
    return out / "lid.parquet"


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    agg_path = settings.results_dir / run_id / "lid_aggregates.parquet"
    if not agg_path.exists() and score_lid(run_id, settings) is None:
        return None
    aggs = records(pd.read_parquet(agg_path)) if agg_path.exists() else None
    if not aggs:
        return None
    return {"lid": {"aggregates": aggs}}
