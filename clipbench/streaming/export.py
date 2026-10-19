"""Export hook for the streaming track: the `streaming` key of web/public/data/runs/<run_id>.json.

`export_section(run_id, settings)` returns None unless results/<run_id>/stream_aggregates.parquet exists
(run `clipbench score-stream <run_id>` first). Numbers and labels only, no text:

    {"kind": "stream", "run_id", "language", "tier", "chunk_ms", "speed", "real_time", "n_clips",
     "providers": [{"id", "model_id", "billing", "usd_per_minute", "pricing_checked_on", "pricing_checked_url",
                    "n_clips", "n_ok", "n_skipped", "n_error", "medians": {"ttfp_ms", "ttfs_ms", "error_rate",
                    "partial_rewrite_rate", "revisions_per_word", "timing_median_ms"}}],
     "aggregates": [one record per stream_aggregates.parquet row, NaN as null]}

Medians are over clips with status ok. Batch numbers never appear here, and this section never feeds a
batch leaderboard.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from clipbench.config import Settings, get_settings

MEDIAN_COLUMNS = ["ttfp_ms", "ttfs_ms", "error_rate", "partial_rewrite_rate", "revisions_per_word", "timing_median_ms"]


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if hasattr(value, "item"):  # numpy scalar
        return _clean(value.item())
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _median(series: pd.Series) -> float | None:
    v = pd.to_numeric(series, errors="coerce").dropna()
    return float(v.median()) if len(v) else None


def export_section(run_id: str, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    agg_path = s.results_dir / run_id / "stream_aggregates.parquet"
    rows_path = s.runs_dir / run_id / "stream_rows.parquet"
    if not agg_path.exists() or not rows_path.exists():
        return None
    meta: dict[str, Any] = {}
    meta_path = s.runs_dir / run_id / "run_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            meta = {}
    if meta and meta.get("kind") != "stream":
        return None
    agg = pd.read_parquet(agg_path)
    rows = pd.read_parquet(rows_path)
    # Medians from the rescored rows when possible, so they match the aggregates.
    from clipbench.streaming.score import rescore

    rows = rescore(rows, s.runs_text_dir / run_id / "stream_texts.parquet", s.runs_text_dir / run_id / "refs.parquet")
    pmeta = {m.get("id"): m for m in meta.get("providers") or [] if isinstance(m, dict)}
    providers = []
    for pid, g in rows.groupby("provider"):
        ok = g[g["status"] == "ok"]
        m = pmeta.get(pid, {})
        providers.append(
            {
                "id": str(pid),
                "model_id": m.get("model_id") or (str(g["model_id"].dropna().iloc[0]) if g["model_id"].notna().any() else None),
                "billing": m.get("billing"),
                "usd_per_minute": _clean(m.get("usd_per_minute")),
                "pricing_checked_on": m.get("pricing_checked_on"),
                "pricing_checked_url": m.get("pricing_checked_url"),
                "n_clips": int(g["video_id"].nunique()),
                "n_ok": int(len(ok)),
                "n_skipped": int((g["status"] == "skipped").sum()),
                "n_error": int((g["status"] == "error").sum()),
                "medians": {c: _median(ok[c]) for c in MEDIAN_COLUMNS},
            }
        )
    records = [{k: _clean(v) for k, v in r.items()} for r in agg.astype(object).to_dict("records")]
    return {
        "kind": "stream",
        "run_id": run_id,
        "language": meta.get("language"),
        "tier": meta.get("tier"),
        "chunk_ms": meta.get("chunk_ms"),
        "speed": meta.get("speed"),
        "real_time": meta.get("real_time"),
        "n_clips": int(rows["video_id"].nunique()),
        "providers": providers,
        "aggregates": records,
    }
