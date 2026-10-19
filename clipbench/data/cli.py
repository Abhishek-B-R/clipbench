"""clipbench data build|sample|fetch-info|fetch-audio."""

from __future__ import annotations

import typer

data_app = typer.Typer(help="Build the dataset from Vako's caption store.", no_args_is_help=True)


@data_app.command("fetch-info")
def fetch_info_cmd(
    lang: str = typer.Option(None, "--lang", help="Language dir under .vr/src (default: settings)."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch every id, not only short ones."),
    max_estimate: float = typer.Option(95.0, help="Only ids whose json3 estimate is at most this many seconds."),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry ids recorded in failed.json."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    limit: int = typer.Option(None, "--limit", help="Fetch at most this many."),
    gap: float = typer.Option(5.0, "--gap", min=5.0, help="Seconds between requests (at least 5)."),
) -> None:
    """Fetch info.json with yt-dlp for videos that have json3 but no info.json."""
    from clipbench.data.info import fetch_info

    s = fetch_info(
        language=lang,
        fetch_all=fetch_all,
        max_estimate_s=max_estimate,
        retry_failed=retry_failed,
        cookies_from_browser=cookies_from_browser,
        limit=limit,
        gap_s=gap,
        log=lambda m: typer.echo(m),
    )
    typer.echo(f"fetched {s.fetched}, failed {s.failed}, skipped (failed before) {s.skipped_failed}")
    if s.stopped:
        typer.echo(f"stopped early: {s.stopped}", err=True)
        raise typer.Exit(2)


@data_app.command("build")
def build_cmd(lang: str = typer.Option(None, "--lang", help="Language (default: settings).")) -> None:
    """Scan Vako, classify tracks from info.json, write data/manifest.parquet."""
    from clipbench.data.manifest import build_manifest, summarize

    df, path = build_manifest(language=lang)
    typer.echo(f"wrote {path}")
    typer.echo(summarize(df))


@data_app.command("sample")
def sample_cmd(
    n: int = typer.Option(150, "--n", help="Target sample size."),
    seed: int = typer.Option(None, "--seed", help="Random seed (default: settings sample_seed)."),
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
) -> None:
    """Stratified sample of creator-captioned clips, 5 to 90 s, no channel over 10%."""
    from clipbench.data.sample import write_sample

    result = write_sample(n=n, seed=seed, language=lang)
    typer.echo(result.message)


@data_app.command("fetch-audio")
def fetch_audio_cmd(
    limit: int = typer.Option(None, "--limit", help="Only the first N sample rows (by sample_rank)."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(10.0, "--gap", min=10.0, help="Seconds between downloads (at least 10)."),
) -> None:
    """Download audio for the sample and convert it to 16 kHz mono WAV."""
    from clipbench.data.audio import fetch_audio

    s = fetch_audio(limit=limit, cookies_from_browser=cookies_from_browser, gap_s=gap, log=lambda m: typer.echo(m))
    typer.echo(f"ready {s.ready} (downloaded {s.downloaded}, already there {s.skipped}), failed {s.failed}")
    if s.stopped:
        typer.echo(f"stopped early: {s.stopped}", err=True)
        raise typer.Exit(2)


def register(app: typer.Typer) -> None:
    app.add_typer(data_app, name="data")
