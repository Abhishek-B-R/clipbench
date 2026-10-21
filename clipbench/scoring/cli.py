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
    for lang in ("en", "ja", "ko", "es", "hi"):
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


def _is_stream_run(run_id: str) -> bool:
    """Streaming runs (run_meta kind "stream") are scored by clipbench.streaming, never mixed with batch."""
    import json

    from clipbench.config import get_settings

    meta = get_settings().runs_dir / run_id / "run_meta.json"
    try:
        return json.loads(meta.read_text()).get("kind") == "stream"
    except (OSError, ValueError):
        return False


def register(app: typer.Typer) -> None:
    @app.command("score")
    def score(run_id: str = typer.Argument(..., help="Run id, or 'latest'.")) -> None:
        """Score a run: scores, aggregates, paired comparisons and stability parquet files."""
        from clipbench.runs import resolve_run_id
        from clipbench.scoring.score import score_run

        try:
            run_id = resolve_run_id(run_id)
            if _is_stream_run(run_id):
                from clipbench.streaming.score import score_stream

                Console().print(f"wrote {score_stream(run_id)}")
                return
            out = score_run(run_id)
        except FileNotFoundError as exc:
            # A typo in the run id is the common case: say what is missing, not a traceback.
            typer.echo(f"{exc}. Is the run id right? Runs are the folders under results/runs/.", err=True)
            raise typer.Exit(1) from exc
        except ValueError as exc:  # e.g. a run holding several languages
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        console = Console()
        table = headline_table(pd.read_parquet(out / "aggregates.parquet"), run_id)
        if table is None:
            console.print("No scored rows.")
        else:
            console.print(table)
        console.print(f"Wrote {out}")
