"""clipbench report <run_id> and clipbench publish --check."""

from __future__ import annotations

import typer


def register(app: typer.Typer) -> None:
    @app.command("report")
    def report(
        run_id: str = typer.Argument(..., help="Run id with scores under results/<run_id>/, or 'latest'."),
    ) -> None:
        """Write results/<run_id>/report.md and site/index.html (with chart PNGs)."""
        from clipbench.report.report import build_report
        from clipbench.runs import resolve_run_id

        try:
            md, page = build_report(resolve_run_id(run_id))
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"wrote {md}")
        typer.echo(f"wrote {page}")

    @app.command("publish")
    def publish(
        check: bool = typer.Option(
            False, "--check", help="Only check site/ and results/ for audio or caption text."
        ),
    ) -> None:
        """Check that site/ and results/ hold no audio and no long reference text."""
        from clipbench.config import get_settings
        from clipbench.report.publish import check as run_check

        settings = get_settings()
        offenders = run_check(settings)
        if offenders:
            typer.echo(f"publish check FAILED: {len(offenders)} offender(s)", err=True)
            for o in offenders:
                typer.echo(f"  {o}", err=True)
            raise typer.Exit(1)
        typer.echo("publish check passed: no audio, no reference spans over 8 words, data/ is gitignored")
        if not check:
            typer.echo(f"deploy with: npx wrangler pages deploy {settings.site_dir}")
