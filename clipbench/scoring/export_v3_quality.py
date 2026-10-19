"""Export hook for the v3 quality metrics: keys `semantic`, `formatting`, `entities`, `style`, `lattice`.

`export_section(run_id, settings)` is called by clipbench.report.export. Every value is numbers only (no
caption text) or None when the run has no such data. Formatting, entities and style are computed from
the run's private texts when their parquet files are missing (free, nothing is written); on a machine
without data/ only the committed results are used.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from clipbench.config import Settings
from clipbench.scoring.quality_io import frame_records


def _read(path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path) if path.exists() else None
    except (OSError, ValueError):
        return None


def _frame_and_aggregates(
    run_id: str, settings: Settings, name: str, frame: Callable, aggregate: Callable
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    out = settings.results_dir / run_id
    df = _read(out / f"{name}.parquet")
    agg = _read(out / f"{name}_aggregates.parquet")
    if df is None:
        df = frame(run_id, settings)
        agg = None
    if df is None or df.empty:
        return None, None
    if agg is None:
        agg = aggregate(df, settings.sample_seed)
    return df, agg


def lattice_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    agg = _read(settings.results_dir / run_id / "aggregates.parquet")
    if agg is None or "error_rate_lattice" not in agg or agg["error_rate_lattice"].isna().all():
        return None
    cols = ["provider", "model_id", "tier", "metric", "slice_kind", "slice", "n_clips", "error_rate", "ci_low",
            "ci_high", "error_rate_lattice", "lattice_ci_low", "lattice_ci_high"]  # fmt: skip
    rows = agg[[c for c in cols if c in agg]]
    return {"aggregates": frame_records(rows) or []}


def formatting_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    from clipbench.scoring.formatting import formatting_aggregates, formatting_frame

    df, agg = _frame_and_aggregates(run_id, settings, "formatting", formatting_frame, formatting_aggregates)
    if df is None:
        return None
    return {
        "n_punctuated_clips": int(df[df["punct_ref_marks"].notna()]["video_id"].nunique()),
        "n_itn_spans": int(df.drop_duplicates("video_id")["itn_n"].fillna(0).sum()),
        "aggregates": frame_records(agg) or [],
    }


def entities_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    from clipbench.scoring.entities import entities_aggregates, entities_frame, export_payload

    df, agg = _frame_and_aggregates(run_id, settings, "entities", entities_frame, entities_aggregates)
    if df is None:
        return None
    return export_payload(run_id, settings, df, agg)


def style_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    from clipbench.scoring.style import style_aggregates, style_frame

    df, agg = _frame_and_aggregates(run_id, settings, "style", style_frame, style_aggregates)
    if df is None:
        return None
    return {"aggregates": frame_records(agg) or []}


def semantic_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    from clipbench.scoring.semantic import export_payload

    return export_payload(run_id, settings)


def export_section(run_id: str, settings: Settings) -> dict[str, Any]:
    return {
        "semantic": semantic_section(run_id, settings),
        "formatting": formatting_section(run_id, settings),
        "entities": entities_section(run_id, settings),
        "style": style_section(run_id, settings),
        "lattice": lattice_section(run_id, settings),
    }
