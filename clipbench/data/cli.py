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
    """Build data/manifest.parquet from datasets/<lang>/clips.csv; tracks classified from info.json."""
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
    """Stratified sample: Shorts first, then windows; categories balanced; no channel over 5%."""
    from clipbench.data.sample import write_sample

    result = write_sample(n=n, seed=seed, language=lang, shorts_only=shorts_only)
    typer.echo(result.message)


@data_app.command("fetch-audio")
def fetch_audio_cmd(
    limit: int = typer.Option(None, "--limit", help="Only the first N sample rows per language (by sample_rank)."),
    lang: str = typer.Option(None, "--lang", help="Only this language (default: every language in the sample)."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(10.0, "--gap", min=10.0, help="Seconds between downloads (at least 10)."),
) -> None:
    """Download audio for the sample and convert it to 16 kHz mono WAV."""
    from clipbench.data.audio import fetch_audio

    s = fetch_audio(
        limit=limit, language=lang, cookies_from_browser=cookies_from_browser, gap_s=gap, log=lambda m: typer.echo(m)
    )
    typer.echo(f"ready {s.ready} (downloaded {s.downloaded}, already there {s.skipped}), failed {s.failed}")
    if s.stopped:
        typer.echo(f"stopped early: {s.stopped}", err=True)
        raise typer.Exit(2)


@data_app.command("discover")
def discover_cmd(
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    max_requests: int = typer.Option(1500, "--max-requests", help="Cap on yt-dlp requests this run."),
    channels: str = typer.Option(None, "--channels", help="Channel list CSV (default datasets/<lang>/channels.csv)."),
    per_channel: int = typer.Option(8, "--per-channel", help="Stop a channel after this many kept Shorts."),
    miss_limit: int = typer.Option(6, "--miss-limit", help="Drop a channel after this many misses with none kept."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(5.0, "--gap", min=5.0, help="Seconds between requests (at least 5)."),
) -> None:
    """Find more Shorts with a creator caption track, from Vako's seed channels."""
    from pathlib import Path

    from clipbench.data.datasets import read_channels
    from clipbench.data.discover import discover

    chan_rows = None
    if channels:
        if not Path(channels).exists():
            raise typer.BadParameter(f"{channels} not found")
        chan_rows = read_channels(Path(channels))
    s = discover(
        language=lang,
        channels=chan_rows,
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
    sample_only: bool = typer.Option(False, "--sample-only", help="Only clips in data/sample.parquet."),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry ids recorded in data/tracks/failed.json."),
    cookies_from_browser: str = typer.Option(None, "--cookies-from-browser", help="Passed to yt-dlp."),
    gap: float = typer.Option(5.0, "--gap", min=5.0, help="Seconds between requests (at least 5)."),
) -> None:
    """For YouTube clips in clips.csv: fetch missing info.json, creator tracks and <lang>-orig.

    Rebuilds the manifest afterwards and refreshes the sample's columns (membership unchanged)."""
    from clipbench.config import get_settings
    from clipbench.data.datasets import read_clips
    from clipbench.data.manifest import build_manifest
    from clipbench.data.sample import load_sample, refresh_sample
    from clipbench.data.tracks import fetch_clip_tracks

    settings = get_settings()
    lang = lang or settings.language
    if sample_only:
        s = load_sample(settings, lang)
        s = s[s["platform"].fillna("youtube") == "youtube"] if "platform" in s else s
        ids = list(s["source_id"] if "source_id" in s else s["video_id"])
    else:
        ids = [r.video_id for r in read_clips(lang, settings) if r.platform == "youtube"]
    res = fetch_clip_tracks(
        ids,
        settings,
        lang,
        retry_failed=retry_failed,
        cookies_from_browser=cookies_from_browser,
        gap_s=gap,
        log=lambda m: typer.echo(m),
    )
    typer.echo(f"fetched {res.fetched}, failed {res.failed}, nothing needed or skipped {res.skipped}")
    build_manifest(language=lang, settings=settings)
    refresh_sample(settings)
    typer.echo("manifest rebuilt, sample refreshed")
    if res.stopped:
        typer.echo(f"stopped early: {res.stopped}", err=True)
        raise typer.Exit(2)


@data_app.command("import-vako")
def import_vako_cmd(
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    seed_channels: bool = typer.Option(
        False, "--seed-channels", help="Also merge the store's channels into datasets/<lang>/channels.csv."
    ),
    replace: bool = typer.Option(False, "--replace", help="Drop the existing vako rows first (rule changes)."),
) -> None:
    """Add clips from the Vako caption store to datasets/<lang>/clips.csv (Shorts and long-video windows)."""
    from clipbench.config import get_settings
    from clipbench.data.importers import import_vako, seed_channels_csv

    settings = get_settings()
    lang = lang or settings.language
    s = import_vako(lang, settings, replace=replace)
    typer.echo(
        f"scanned {s.scanned}: {s.shorts} Shorts, {s.windows} windows; skipped: no info {s.no_info}, "
        f"no creator track {s.no_creator}, no clean window {s.no_window}, under 5 s {s.too_short}"
    )
    typer.echo(f"clips.csv: {s.removed} vako rows removed, {s.added} added, {s.already} already there")
    if seed_channels:
        path, total, added = seed_channels_csv(lang, settings)
        typer.echo(f"{path}: {total} channels ({added} added)")


@data_app.command("import-ids")
def import_ids_cmd(
    csv_path: str = typer.Argument(..., help="CSV with video_id and optional platform, start_ms, end_ms, channel_id, category, license_note."),
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    source: str = typer.Option("ids", "--source", help="Value for the source column."),
) -> None:
    """Add any list of public ids to datasets/<lang>/clips.csv."""
    from pathlib import Path

    from clipbench.config import get_settings
    from clipbench.data.datasets import SpecError
    from clipbench.data.importers import import_ids

    settings = get_settings()
    try:
        s = import_ids(Path(csv_path), lang or settings.language, settings, source=source)
    except (SpecError, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"read {s.scanned} rows: {s.added} added, {s.already} already there")


@data_app.command("check")
def check_cmd(
    lang: str = typer.Option(None, "--lang", help="Language (default: settings)."),
    pool: bool = typer.Option(False, "--pool", help="Check the eligible manifest pool instead of the sample."),
    strict: bool = typer.Option(False, "--strict", help="Do not count guessed categories."),
    verified: bool = typer.Option(
        False, "--verified", help="Check only the sample's verified clips, with categories from the verified files."
    ),
) -> None:
    """Diversity rules: >= 30 channels, no channel above 5%, >= 6 categories. Exit 1 when they fail."""
    from clipbench.config import get_settings
    from clipbench.data.check import check_set, check_verified, verified_sample
    from clipbench.data.manifest import load_manifest
    from clipbench.data.sample import eligible, load_sample

    settings = get_settings()
    lang = lang or settings.language
    try:
        if pool:
            m = load_manifest(settings)
            df, what = eligible(m[m["language"] == lang]), "eligible pool"
        else:
            df, what = load_sample(settings, lang), "sample"
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if verified:
        from clipbench.data.info import read_json

        df = verified_sample(df, settings.verified_dir, read_json(settings.audio_dir / "hashes.json", {}))
        res, what = check_verified(df, settings.verified_dir), f"verified clips of the {what}"
    else:
        res = check_set(df, strict=strict)
    typer.echo(res.report(lang, what))
    if not res.ok:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.add_typer(data_app, name="data")
