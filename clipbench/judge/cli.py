"""`clipbench judge run|label|calibrate` and `clipbench quality <run_id>`."""

from __future__ import annotations

import typer
from rich.console import Console


def register(app: typer.Typer) -> None:
    judge = typer.Typer(help="Semantic WER: LLM judge, human labels and calibration.", no_args_is_help=True)
    app.add_typer(judge, name="judge")

    @judge.command("run")
    def run(
        run_id: str = typer.Argument(..., help="Run id, or 'latest'."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Price the run and stop; no request is sent."),
        model: str = typer.Option(None, help="Judge model id (default: the pinned Workers AI model)."),
        prompt: str = typer.Option(None, help="Prompt version under clipbench/judge/prompts."),
    ) -> None:
        """Judge every verified pair of a run; writes semantic.parquet and semantic_aggregates.parquet."""
        from clipbench.config import get_settings
        from clipbench.judge.client import DEFAULT_MODEL, DEFAULT_PROMPT
        from clipbench.judge.run import judge_run
        from clipbench.runs import resolve_run_id

        console = Console()
        model_, prompt_ = model or DEFAULT_MODEL, prompt or DEFAULT_PROMPT
        res = judge_run(resolve_run_id(run_id), get_settings(), dry_run=dry_run, model=model_, prompt=prompt_)
        console.print(res.plan.describe(model_, prompt_))
        if dry_run:
            console.print("dry run: nothing sent, nothing written" + ("" if res.plan.fits else " (over the cap)"))
            return
        console.print(f"scored {res.n_scored} pairs, {res.n_failed} failed; wrote {res.out_dir}")

    @judge.command("label")
    def label(
        lang: str = typer.Option(..., "--lang", help="Language to label."),
        labeller: str = typer.Option(..., "--labeller", help="Your name, stored with each label."),
        n: int = typer.Option(60, help="Pairs to label in this session."),
        seed: int = typer.Option(7, help="Sampling seed."),
    ) -> None:
        """Score real (reference, hypothesis) pairs by hand. Labels go to data/calibration/ (never committed)."""
        from clipbench.config import get_settings
        from clipbench.judge.run import label_session

        console = Console()
        label_session(get_settings(), lang, labeller, n=n, seed=seed, ask=input, show=console.print)

    @judge.command("calibrate")
    def calibrate(
        lang: str = typer.Option(None, "--lang", help="Only this language."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Price the calibration and stop."),
        model: str = typer.Option(None, help="Judge model id."),
        prompt: str = typer.Option(None, help="Prompt version."),
    ) -> None:
        """Agreement (Spearman, mean absolute error) between the judge and the human labels, per language."""
        from clipbench.config import get_settings
        from clipbench.judge.client import DEFAULT_MODEL, DEFAULT_PROMPT
        from clipbench.judge.run import calibrate as do_calibrate

        console = Console()
        model_, prompt_ = model or DEFAULT_MODEL, prompt or DEFAULT_PROMPT
        res = do_calibrate(get_settings(), language=lang, dry_run=dry_run, model=model_, prompt=prompt_)
        console.print(res.plan.describe(model_, prompt_))
        if res.calibration is None:
            console.print("dry run: nothing sent, nothing written")
            return
        for language, st in sorted(res.calibration["languages"].items()):
            console.print(
                f"{language}: n={st['n_pairs']} spearman={st['spearman']} mae={st['mae']} "
                f"{'PASS' if st['passes'] else 'not published'} ({st['reason']})"
            )

    @app.command("quality")
    def quality(run_id: str = typer.Argument(..., help="Run id, or 'latest'.")) -> None:
        """Formatting, entity and style metrics for a run (free, no requests)."""
        from clipbench.config import get_settings
        from clipbench.runs import resolve_run_id
        from clipbench.scoring.entities import score_entities
        from clipbench.scoring.formatting import score_formatting
        from clipbench.scoring.style import score_style

        console = Console()
        run_id = resolve_run_id(run_id)
        s = get_settings()
        for name, fn in (("formatting", score_formatting), ("entities", score_entities), ("style", score_style)):
            out = fn(run_id, s)
            console.print(f"{name}: " + (f"wrote {out}" if out else "run texts not on this machine"))
