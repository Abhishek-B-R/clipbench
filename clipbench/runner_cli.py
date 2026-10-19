"""`clipbench run`: run providers over clips, with a budget check before any money is spent."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console


def register(app: typer.Typer) -> None:
    @app.command("run")
    def run_cmd(
        providers: str = typer.Option("all", "--providers", help="'all' or a comma separated list of ids."),
        tier: str = typer.Option("verified", "--tier", help="verified|creator"),
        lang: str | None = typer.Option(
            None, "--lang", help="Language of the run (a run holds one). Required when the sample has several."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and cost estimate. No network."),
        no_cache: bool = typer.Option(False, "--no-cache", help="Call providers even when a cached result exists."),
        limit: int | None = typer.Option(None, "--limit", help="Only the first N clips (sorted by video id)."),
        run_id: str | None = typer.Option(None, "--run-id", help="Resume or name a run."),
        stability: bool = typer.Option(
            True, "--stability/--no-stability", help="Rerun 20 seeded clips 3 times per paid provider."
        ),
    ) -> None:
        """Run speech-to-text providers over the sample and write results/runs/<run_id>/."""
        from clipbench.budget import BudgetExceeded
        from clipbench.config import get_settings
        from clipbench.providers.registry import resolve
        from clipbench.runner import MixedLanguages, NoClips, run

        console = Console(stderr=True)
        if tier not in ("verified", "creator"):
            raise typer.BadParameter("--tier must be verified or creator")
        try:
            ids = resolve(providers)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        settings = get_settings()
        try:
            result = asyncio.run(
                run(
                    ids,
                    tier,
                    run_id=run_id,
                    no_cache=no_cache,
                    dry_run=dry_run,
                    limit=limit,
                    seed=settings.sample_seed,
                    settings=settings,
                    console=console,
                    stability=stability,
                    language=lang,
                )
            )
        except MixedLanguages as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        except NoClips as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        except BudgetExceeded as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        if dry_run:
            if not result.allowed:
                raise typer.Exit(2)
            return
        df = result.rows
        console.print(f"run {result.run_id}: {len(df) if df is not None else 0} rows -> {result.rows_path}")
        if df is not None and len(df):
            counts = df.groupby(["provider", "status"]).size().unstack(fill_value=0)
            console.print(counts.to_string())
        typer.echo(result.run_id)
