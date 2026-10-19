"""`clipbench review`: the local review UI that builds the verified reference set."""

from __future__ import annotations

import getpass
import os

import typer

HOST = "127.0.0.1"  # never 0.0.0.0: creator content stays on this machine


def register(app: typer.Typer) -> None:
    @app.command()
    def review(
        reviewer: str = typer.Option(None, "--reviewer", help="Name stored in each verified file."),
        port: int = typer.Option(8765, "--port", help="Port on 127.0.0.1."),
        only_rereview: bool = typer.Option(
            False, "--only-rereview", help="Only clips listed in data/rereview.json."
        ),
    ) -> None:
        """Review creator captions against the audio and save verified references."""
        import uvicorn

        from clipbench.config import get_settings
        from clipbench.review.server import create_app

        name = reviewer or os.environ.get("CLIPBENCH_REVIEWER") or getpass.getuser()
        settings = get_settings()
        web = create_app(settings, reviewer=name, only_rereview=only_rereview)
        if not settings.sample_path.exists():
            typer.echo(f"warning: {settings.sample_path} not found, the queue is empty", err=True)
        typer.echo(f"clipbench review: http://{HOST}:{port}/  (reviewer: {name}, ctrl+c to stop)")
        uvicorn.run(web, host=HOST, port=port, log_level="warning")
