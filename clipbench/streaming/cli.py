"""`clipbench run-stream` and `clipbench score-stream`: the streaming track's commands."""

from __future__ import annotations

import asyncio

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table


def _pct(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{100 * x:.1f}%"


def _ms(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:.0f}"


def headline(agg: pd.DataFrame, run_id: str) -> Table | None:
    df = agg[agg["slice_kind"] == "all"].sort_values("error_rate")
    if df.empty:
        return None
    table = Table(title=f"clipbench streaming {run_id}")
    for col in ("provider", "model", "metric", "final error", "95% CI", "n", "TTFP p50", "TTFS p50",
                "rewritten", "revisions/word"):  # fmt: skip
        table.add_column(col)
    for r in df.itertuples(index=False):
        table.add_row(
            r.provider, str(r.model_id), str(r.metric), _pct(r.error_rate),
            "-" if pd.isna(r.ci_low) else f"{100 * r.ci_low:.1f}-{100 * r.ci_high:.1f}",
            f"{r.n_clips}" + (" (small)" if r.too_small else ""), _ms(r.ttfp_p50_ms), _ms(r.ttfs_p50_ms),
            _pct(r.partial_rewrite_rate), "-" if pd.isna(r.revisions_per_word) else f"{r.revisions_per_word:.2f}",
        )  # fmt: skip
    return table


def register(app: typer.Typer) -> None:
    @app.command("run-stream")
    def run_stream_cmd(
        providers: str = typer.Option("all", "--providers", help="'all' or a comma separated list of *-stream ids."),
        tier: str = typer.Option("verified", "--tier", help="verified|creator"),
        lang: str | None = typer.Option(None, "--lang", help="Language of the run (a run holds one)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and cost estimate. No network."),
        limit: int | None = typer.Option(None, "--limit", help="Only the first N clips (sorted by clip id)."),
        chunk_ms: int = typer.Option(100, "--chunk-ms", help="Audio per WebSocket message, in ms."),
        speed: float = typer.Option(1.0, "--speed", help="Pace factor; 1 is real time. Other values are for tests."),
        run_id: str | None = typer.Option(None, "--run-id", help="Resume or name a run."),
        no_cache: bool = typer.Option(False, "--no-cache", help="Stream even when a cached session exists."),
        concurrency: int | None = typer.Option(None, "--concurrency", help="Sessions at once per provider."),
    ) -> None:
        """Stream clips through realtime providers at real-time pace; writes a streaming run (kind=stream)."""
        from clipbench.budget import BudgetExceeded
        from clipbench.config import get_settings
        from clipbench.streaming.registry import resolve
        from clipbench.streaming.runner import MixedLanguages, NoClips, run_stream

        console = Console(stderr=True)
        if tier not in ("verified", "creator"):
            raise typer.BadParameter("--tier must be verified or creator")
        if speed <= 0:
            raise typer.BadParameter("--speed must be positive")
        if chunk_ms <= 0:
            raise typer.BadParameter("--chunk-ms must be positive")
        try:
            ids = resolve(providers)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        try:
            result = asyncio.run(
                run_stream(ids, tier, language=lang, dry_run=dry_run, limit=limit, chunk_ms=chunk_ms, speed=speed,
                           run_id=run_id, no_cache=no_cache, concurrency=concurrency, settings=get_settings(),
                           console=console)
            )  # fmt: skip
        except (MixedLanguages, BudgetExceeded) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        except NoClips as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        if dry_run:
            if not result.allowed:
                raise typer.Exit(2)
            return
        df = result.rows
        console.print(f"streaming run {result.run_id}: {0 if df is None else len(df)} rows -> {result.rows_path}")
        if df is not None and len(df):
            console.print(df.groupby(["provider", "status"]).size().unstack(fill_value=0).to_string())
        typer.echo(result.run_id)

    @app.command("score-stream")
    def score_stream_cmd(run_id: str = typer.Argument(..., help="Streaming run id, or 'latest'.")) -> None:
        """Score a streaming run: results/<run_id>/stream_aggregates.parquet with 95% bootstrap CIs."""
        from clipbench.streaming.score import resolve_stream_run_id, score_stream

        try:
            run_id = resolve_stream_run_id(run_id)
            out = score_stream(run_id)
        except FileNotFoundError as exc:
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        console = Console()
        table = headline(pd.read_parquet(out), run_id)
        console.print(table if table is not None else "No scored rows.")
        console.print(f"Wrote {out}")
