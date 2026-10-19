"""`clipbench providers features`: print the B20 feature matrix from pricing.toml."""

from __future__ import annotations

import json

import typer

from clipbench.providers.features import FEATURE_KEYS, feature_matrix

SHORT = {
    "languages": "langs",
    "batch": "batch",
    "streaming": "stream",
    "word_timestamps": "word ts",
    "diarization": "diarize",
    "language_id": "LID",
    "custom_vocabulary": "vocab",
    "max_file_minutes": "max min",
    "retention": "retention",
    "training_opt_out": "training opt-out",
}


def _cell(value) -> str:
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)


def features(
    as_json: bool = typer.Option(False, "--json", help="Print the matrix as JSON, with doc URLs and dates."),
    urls: bool = typer.Option(False, "--urls", help="Also list each cell's doc URL and check date."),
) -> None:
    """Print the provider feature matrix (languages, streaming, diarization, LID, vocabulary, limits, data)."""
    matrix = feature_matrix()
    if as_json:
        typer.echo(json.dumps(matrix, indent=2, ensure_ascii=False))
        return
    from rich.console import Console
    from rich.table import Table

    table = Table(title="clipbench provider features (pricing.toml)")
    table.add_column("provider")
    table.add_column("USD/min", justify="right")
    for key in FEATURE_KEYS:
        table.add_column(SHORT[key], overflow="fold")
    for rec in matrix:
        price = rec.get("usd_per_minute")
        table.add_row(
            rec["provider"],
            "?" if price is None else f"{float(price):.5f}",
            *[_cell(rec["features"][k]["value"]) for k in FEATURE_KEYS],
        )
    console = Console()
    console.print(table)
    if urls:
        for rec in matrix:
            for key in FEATURE_KEYS:
                cell = rec["features"][key]
                note = f" ({cell['note']})" if cell.get("note") else ""
                console.print(f"{rec['provider']}.{key}: {cell['url']} checked {cell['checked_on']}{note}")


def register(app: typer.Typer) -> None:
    group = typer.Typer(help="Provider capabilities and prices.", no_args_is_help=True)
    group.command("features")(features)
    app.add_typer(group, name="providers")
