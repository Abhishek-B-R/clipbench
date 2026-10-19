"""`clipbench sweep build|fetch-beds|beds|run|score`."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

sweep_app = typer.Typer(help="Controlled SNR sweeps: clean speech mixed with music and noise beds.", no_args_is_help=True)


def _floats(spec: str) -> list[float]:
    try:
        return [float(x) for x in spec.split(",") if x.strip()]
    except ValueError as exc:
        raise typer.BadParameter(f"not a list of numbers: {spec!r}") from exc


def _list(spec: str | None) -> list[str] | None:
    if not spec:
        return None
    return [x.strip() for x in spec.split(",") if x.strip()]


@sweep_app.command("beds")
def beds_cmd() -> None:
    """List the beds in datasets/beds/beds.json with license, usability and whether they are on disk."""
    from clipbench.sweeps.beds import bed_available, load_manifest

    table = Table(title="sweep beds")
    for col in ("id", "kind", "license", "usable", "on disk", "listened"):
        table.add_column(col)
    for b in load_manifest():
        table.add_row(b.id, b.kind, str(b.license), "yes" if b.usable else "no", "yes" if bed_available(b) else "no",
                      "yes" if b.listened else "no")  # fmt: skip
    Console().print(table)


@sweep_app.command("fetch-beds")
def fetch_beds_cmd(
    only: str | None = typer.Option(None, "--only", help="Comma separated bed ids (default: every usable bed)."),
) -> None:
    """Download the usable beds from the manifest into data/beds/ (never published) and convert to 16 kHz WAV."""
    from clipbench.sweeps.beds import fetch_beds

    summary = fetch_beds(only=_list(only))
    typer.echo(f"ready {len(summary.ready)}, downloaded {len(summary.downloaded)}, failed {len(summary.failed)}, "
               f"excluded {len(summary.excluded)}")  # fmt: skip
    for bid, why in {**summary.excluded, **summary.failed}.items():
        typer.echo(f"  {bid}: {why}")
    if summary.failed:
        raise typer.Exit(1)


@sweep_app.command("build")
def build_cmd(
    lang: str = typer.Option(..., "--lang", help="Language of the clean verified clips."),
    snr: str = typer.Option("20,10,5,0,-5", "--snr", help="Comma separated SNRs in dB."),
    reverb: bool = typer.Option(False, "--reverb", help="Also build every condition with synthetic reverb."),
    rt60: float = typer.Option(0.5, "--rt60", help="Reverb time (RT60) in seconds for --reverb."),
    beds: str = typer.Option("music,noise", "--beds", help="Bed kinds to mix in: music, noise or both."),
    bed_ids: str | None = typer.Option(None, "--bed-ids", help="Only these bed ids."),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N clean clips (by clip id)."),
    seed: int = typer.Option(7, "--seed", help="Seed for bed choice, bed offsets and the reverb impulse response."),
    sweep_id: str | None = typer.Option(None, "--sweep-id", help="Name the sweep (default <lang>-<date>-<hex>)."),
) -> None:
    """Mix clean verified clips with beds at exact SNRs; WAVs to data/sweeps/, recipes to results/sweeps/."""
    from clipbench.sweeps.build import build_sweep

    try:
        res = build_sweep(lang, _floats(snr), reverb=reverb, rt60_s=rt60, kinds=_list(beds) or [],
                          bed_ids=_list(bed_ids), limit=limit, seed=seed, sweep_id=sweep_id)  # fmt: skip
    except (ValueError, FileNotFoundError) as exc:
        Console(stderr=True).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    Console(stderr=True).print(
        f"sweep {res.sweep_id}: {res.n_source_clips} clips, {len(res.recipes)} variants -> {res.recipes_path}"
    )
    typer.echo(res.sweep_id)


@sweep_app.command("run")
def run_cmd(
    sweep_id: str = typer.Argument(..., help="Sweep id from `clipbench sweep build`."),
    providers: str = typer.Option(..., "--providers", help="Comma separated provider ids (or 'all')."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and cost estimate. No network."),
    no_cache: bool = typer.Option(False, "--no-cache"),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N source clips."),
) -> None:
    """Run batch providers over every variant of a sweep (budget guard and cache as in `clipbench run`)."""
    from clipbench.budget import BudgetExceeded
    from clipbench.providers.registry import resolve
    from clipbench.sweeps.run import run_sweep

    console = Console(stderr=True)
    try:
        ids = resolve(providers)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        res = asyncio.run(run_sweep(sweep_id, ids, dry_run=dry_run, no_cache=no_cache, limit_clips=limit,
                                    console=console))  # fmt: skip
    except (FileNotFoundError, BudgetExceeded) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if dry_run:
        raise typer.Exit(0 if res.allowed else 2)
    console.print(f"sweep {sweep_id}: {0 if res.rows is None else len(res.rows)} rows -> {res.rows_path}")


@sweep_app.command("score")
def score_cmd(sweep_id: str = typer.Argument(..., help="Sweep id.")) -> None:
    """Write results/sweeps/<sweep_id>/sweeps.parquet: error rate and hallucination per SNR, doubling SNR."""
    import pandas as pd

    from clipbench.sweeps.score import score_sweep

    try:
        path = score_sweep(sweep_id)
    except FileNotFoundError as exc:
        Console(stderr=True).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    df = pd.read_parquet(path)
    df = df[df["is_primary"]]
    table = Table(title=f"sweep {sweep_id} (primary metric)")
    for col in ("provider", "condition", "n", "error", "x clean", "halluc/min", "doubling SNR"):
        table.add_column(col)
    for r in df.itertuples(index=False):
        dbl = "-" if pd.isna(r.doubling_snr_db) else f"{r.doubling_snr_db:.1f} dB"
        if isinstance(r.doubling_censored, str):
            dbl = {"above": f">= {r.doubling_snr_db:g} dB", "below": "never"}[r.doubling_censored]
        table.add_row(r.provider, r.condition, str(r.n_clips), f"{100 * r.error_rate:.1f}%",
                      "-" if pd.isna(r.rel_to_clean) else f"{r.rel_to_clean:.2f}",
                      "-" if pd.isna(r.halluc_per_nonspeech_min) else f"{r.halluc_per_nonspeech_min:.2f}",
                      dbl if r.condition != "clean" and not r.condition.startswith("clean") else "")  # fmt: skip
    Console().print(table)
    typer.echo(str(path))


def register(app: typer.Typer) -> None:
    app.add_typer(sweep_app, name="sweep")
