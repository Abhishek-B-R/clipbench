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
    probed_creator: bool = typer.Option(
        False, "--probed-creator", help="Target long videos Vako's probe saw with a creator track (for windows)."
    ),
    per_channel: int = typer.Option(None, "--per-channel", help="With --probed-creator: at most N per channel."),
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
        probed_creator=probed_creator,
        per_channel=per_channel,
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
    shorts_only: bool = typer.Option(False, "--shorts-only", help="Do not fill with windows from long videos."),
) -> None:
    """Stratified sample: creator-captioned Shorts first, then windows; no channel over 10%."""
    from clipbench.data.sample import write_sample

    result = write_sample(n=n, seed=seed, language=lang, shorts_only=shorts_only)
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


@data_app.command("discover")
def discover_cmd(
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    max_requests: int = typer.Option(1500, "--max-requests", help="Cap on yt-dlp requests this run."),
    per_channel: int = typer.Option(15, "--per-channel", help="Stop a channel after this many kept Shorts."),
    miss_limit: int = typer.Option(6, "--miss-limit", help="Drop a channel after this many misses with none kept."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(5.0, "--gap", min=5.0, help="Seconds between requests (at least 5)."),
) -> None:
    """Find more Shorts with a creator caption track, from Vako's seed channels."""
    from clipbench.data.discover import discover

    s = discover(
        language=lang,
        max_requests=max_requests,
        per_channel=per_channel,
        miss_limit=miss_limit,
        cookies_from_browser=cookies_from_browser,
        gap_s=gap,
        log=lambda m: typer.echo(m),
    )
    typer.echo(
        f"requests {s.requests}, channels listed {s.channels_listed}, Shorts inspected {s.inspected}, kept {s.kept}"
    )
    typer.echo("run `clipbench data build` to add them to the manifest")
    if s.stopped and s.stopped != "max requests":
        typer.echo(f"stopped early: {s.stopped}", err=True)
        raise typer.Exit(2)


@data_app.command("fetch-tracks")
def fetch_tracks_cmd(
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    pool: bool = typer.Option(False, "--pool", help="Every eligible clip in the manifest, not only the sample."),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry ids recorded in data/tracks/failed.json."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(5.0, "--gap", min=5.0, help="Seconds between requests (at least 5)."),
) -> None:
    """Fetch the <lang>-orig ASR track for creator-captioned clips that have none (for youtube-auto).

    Rebuilds the manifest afterwards and refreshes the sample's columns (membership unchanged)."""
    from clipbench.config import get_settings
    from clipbench.data.manifest import build_manifest, load_manifest
    from clipbench.data.sample import eligible, load_sample, refresh_sample
    from clipbench.data.tracks import fetch_auto_tracks

    settings = get_settings()
    lang = lang or settings.language
    rows = eligible(load_manifest(settings)) if pool else load_sample(settings)
    rows = rows[(rows["language"] == lang) & rows["has_creator_track"] & ~rows["has_auto_track"].astype(bool)]
    ids = list(rows["youtube_id"] if "youtube_id" in rows else rows["video_id"])
    s = fetch_auto_tracks(
        ids,
        settings,
        lang,
        retry_failed=retry_failed,
        cookies_from_browser=cookies_from_browser,
        gap_s=gap,
        log=lambda m: typer.echo(m),
    )
    typer.echo(f"fetched {s.fetched}, failed {s.failed}, skipped {s.skipped}")
    if s.fetched:
        build_manifest(language=lang, settings=settings)
        refresh_sample(settings)
        typer.echo("manifest rebuilt, sample refreshed")
    if s.stopped:
        typer.echo(f"stopped early: {s.stopped}", err=True)
        raise typer.Exit(2)


def register(app: typer.Typer) -> None:
    app.add_typer(data_app, name="data")
