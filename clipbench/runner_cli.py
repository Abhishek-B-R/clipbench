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
        diarize: bool = typer.Option(False, "--diarize", help="Ask providers for speaker labels."),
        detect_language: bool = typer.Option(
            False, "--detect-language", help="Send no language hint; let providers detect it."
        ),
        parallel_providers: bool = typer.Option(
            False, "--parallel-providers",
            help="Run providers at the same time (faster, but latency numbers become contended and are not comparable).",
        ),
        max_concurrency: int = typer.Option(4, "--max-concurrency", help="Most requests in flight per provider."),
        keyterms: str | None = typer.Option(
            None, "--keyterms", help="File with one term per line for custom vocabulary features."
        ),
        allow_unverified: str | None = typer.Option(
            None,
            "--allow-unverified",
            help="Comma separated provider ids to run although their price or data policy is not documented "
            "(recorded in run_meta). Without it a real run refuses them.",
        ),
    ) -> None:
        """Run speech-to-text providers over the sample and write results/runs/<run_id>/."""
        from clipbench.budget import BudgetExceeded
        from clipbench.config import get_settings
        from clipbench.providers.registry import resolve
        from clipbench.runner import MixedLanguages, NoClips, UnverifiedProviders, run

        console = Console(stderr=True)
        if tier not in ("verified", "creator"):
            raise typer.BadParameter("--tier must be verified or creator")
        from clipbench.report.rules import LANGUAGES

        if lang is not None and lang.split("-")[0].lower() not in LANGUAGES:
            raise typer.BadParameter(f"--lang must be one of {', '.join(LANGUAGES)}, got {lang!r}")
        try:
            ids = resolve(providers)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        allow = _allow_ids(allow_unverified)
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
                    options=_run_options(diarize, detect_language, keyterms),
                    allow_unverified=allow,
                    sequential_providers=not parallel_providers,
                    max_concurrency=max_concurrency,
                )
            )
        except MixedLanguages as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        except NoClips as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        except (BudgetExceeded, UnverifiedProviders) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        if dry_run:
            if not result.allowed:
                raise typer.Exit(2)
            if not any(e.n_clips for e in result.estimate.providers.values()):
                raise typer.Exit(1)  # the "no clips" message is above the plan
            return
        df = result.rows
        console.print(f"run {result.run_id}: {len(df) if df is not None else 0} rows -> {result.rows_path}")
        if df is not None and len(df):
            counts = df.groupby(["provider", "status"]).size().unstack(fill_value=0)
            console.print(counts.to_string())
        hint = resume_hint(result, tier, lang)
        if hint:
            console.print(f"[yellow]{hint}[/yellow]")
        typer.echo(result.run_id)


def resume_hint(result, tier: str, lang: str | None) -> str | None:
    """How to fill in the clips of providers that the account quota or the budget cap stopped."""
    import json

    if result.meta_path is None or not result.meta_path.exists():
        return None
    try:
        meta = json.loads(result.meta_path.read_text())
    except (OSError, ValueError):
        return None
    quota = list(meta.get("quota_stopped") or [])
    budget = list(meta.get("budget_stopped") or [])
    if not quota and not budget:
        return None
    parts = []
    if quota:
        parts.append(f"{', '.join(quota)} stopped: account out of quota (top it up)")
    if budget:
        parts.append(f"{', '.join(budget)} stopped: budget cap reached (raise budget_usd)")
    who = ",".join(dict.fromkeys(quota + budget))
    cmd = f"clipbench run --run-id {result.run_id} --tier {tier}" + (f" --lang {lang}" if lang else "")
    return "; ".join(parts) + f". Their missing clips are retried on resume: {cmd} --providers {who}"


def _allow_ids(spec: str | None) -> frozenset[str]:
    """--allow-unverified ids, each a registered batch provider."""
    from clipbench.providers.registry import REGISTRY
    from clipbench.providers.verification import parse_allow

    ids = parse_allow(spec)
    unknown = sorted(ids - set(REGISTRY))
    if unknown:
        raise typer.BadParameter(f"--allow-unverified: unknown provider {', '.join(unknown)}")
    return ids


def _run_options(diarize: bool, detect_language: bool, keyterms: str | None) -> dict:
    from pathlib import Path

    opts: dict = {}
    if diarize:
        opts["diarize"] = True
    if detect_language:
        opts["detect_language"] = True
    if keyterms:
        path = Path(keyterms)
        if not path.exists():
            raise typer.BadParameter(f"--keyterms file not found: {keyterms}")
        terms = [t.strip() for t in path.read_text().splitlines() if t.strip() and not t.startswith("#")]
        opts["keyterms"] = terms
    return opts
