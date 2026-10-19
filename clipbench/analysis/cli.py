"""clipbench errors <run_id>."""

from __future__ import annotations

import typer


def register(app: typer.Typer) -> None:
    @app.command("errors")
    def errors(run_id: str = typer.Argument(..., help="Run id under data/runs/, or 'latest'.")) -> None:
        """Error analysis: top substitutions, worst clips, clips every provider fails."""
        from clipbench.analysis.errors import ALL_FAIL_THRESHOLD, run_errors
        from clipbench.config import get_settings
        from clipbench.runs import resolve_run_id

        settings = get_settings()
        try:
            run_id = resolve_run_id(run_id, settings)
            result = run_errors(run_id, settings)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        for pid, info in result["providers"].items():
            top = ", ".join(f"{r}->{h} x{n}" for r, h, n in info["top_substitutions"][:5]) or "none"
            typer.echo(f"{pid}: {top}")
        typer.echo(
            f"{len(result['all_fail'])} clips where every provider is above {ALL_FAIL_THRESHOLD} "
            f"(written to {settings.data_dir / 'rereview.json'})"
        )
        typer.echo(f"wrote {settings.results_dir / run_id / 'errors.json'}")
