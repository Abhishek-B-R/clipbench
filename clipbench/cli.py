"""clipbench command line. Each feature module registers its own commands."""

from __future__ import annotations

import importlib

import typer

app = typer.Typer(help="clipbench: speech-to-text on real short-form video.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """clipbench: speech-to-text on real short-form video."""

# Modules exposing `register(app: typer.Typer) -> None`. Missing ones are skipped so the
# CLI still loads while a feature is being built.
_MODULES = [
    "clipbench.data.cli",  # clipbench data build|sample|fetch-info|fetch-audio
    "clipbench.review.cli",  # clipbench review
    "clipbench.runner_cli",  # clipbench run
    "clipbench.scoring.cli",  # clipbench score
    "clipbench.align",  # clipbench align
    "clipbench.analysis.cli",  # clipbench errors
    "clipbench.report.cli",  # clipbench report, clipbench publish
    "clipbench.report.export",  # clipbench export
]

for _name in _MODULES:
    try:
        importlib.import_module(_name).register(app)
    except ModuleNotFoundError as exc:
        if exc.name != _name:
            raise


@app.command()
def version() -> None:
    """Print the clipbench version."""
    from clipbench import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
