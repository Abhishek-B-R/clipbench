"""`clipbench score <run_id>`."""

from __future__ import annotations

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table


def _fmt_pct(x: float) -> str:
    return "-" if pd.isna(x) else f"{100 * x:.1f}%"


def _fmt_ci(low: float, high: float) -> str:
    return "-" if pd.isna(low) else f"{100 * low:.1f}-{100 * high:.1f}"


def _fmt(x: float, spec: str) -> str:
    return "-" if pd.isna(x) else format(x, spec)


def headline_table(aggregates: pd.DataFrame, run_id: str) -> Table | None:
    """Primary metric, slice all, for the verified tier if present, else creator."""
    if aggregates.empty:
        return None
    tiers = set(aggregates["tier"])
    tier = "verified" if "verified" in tiers else "creator"
    df = aggregates[(aggregates["tier"] == tier) & (aggregates["slice_kind"] == "all")]
    if df.empty:
        return None
    from clipbench.scoring.normalize import metrics_for

    primary_names = set()
    for lang in ("ja", "es", "ko"):
        primary_names.add(metrics_for(lang)[0].name)
    df = df[df["metric"].isin(primary_names)].sort_values("error_rate")
    label = "verified reference" if tier == "verified" else "creator captions, unverified reference"
    table = Table(title=f"clipbench {run_id}: {label}")
    for col in ("provider", "model", "metric", "error", "95% CI", "n", "halluc.", "$/hour", "p50 ms"):
        table.add_column(col)
    for r in df.itertuples(index=False):
        n = f"{r.n_clips}" + (" (small)" if r.too_small else "")
        table.add_row(
            r.provider,
            r.model_id,
            r.metric,
            _fmt_pct(r.error_rate),
            _fmt_ci(r.ci_low, r.ci_high),
            n,
            _fmt_pct(r.hallucination_rate),
            _fmt(r.cost_per_hour, ".2f"),
            _fmt(r.latency_p50, ".0f"),
        )
    return table


def register(app: typer.Typer) -> None:
    @app.command("score")
    def score(run_id: str = typer.Argument(..., help="Run id, or 'latest'.")) -> None:
        """Score a run: scores, aggregates, paired comparisons and stability parquet files."""
        from clipbench.runs import resolve_run_id
        from clipbench.scoring.score import score_run

        run_id = resolve_run_id(run_id)
        out = score_run(run_id)
        console = Console()
        table = headline_table(pd.read_parquet(out / "aggregates.parquet"), run_id)
        if table is None:
            console.print("No scored rows.")
        else:
            console.print(table)
        console.print(f"Wrote {out}")
