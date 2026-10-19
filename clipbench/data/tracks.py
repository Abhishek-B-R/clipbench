"""Download json3 caption tracks into data/tracks/ with yt-dlp.

Used by `data discover` (creator + `<lang>-orig` for new Shorts) and `data fetch-tracks` (the
`<lang>-orig` ASR track for clips that only have a creator track, so youtube-auto has an output).
Files land as `data/tracks/<videoId>.<key>.json3` and are classified like Vako's, from info.json.
"""

from __future__ import annotations

import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from clipbench.config import Settings, get_settings
from clipbench.data.info import is_blocked, last_error_line, read_json, watch_url, write_json_atomic
from clipbench.data.vako import find_info, load_info, tracks_dir


def download_tracks(
    video_id: str,
    out_dir: Path,
    sub_langs: str,
    *,
    creator: bool = True,
    auto: bool = True,
    info_json: Path | None = None,
    cookies_from_browser: str | None = None,
    timeout_s: int = 180,
) -> tuple[bool, bool, str]:
    """Returns (ok, blocked, error). With `info_json` (a full, unslimmed yt-dlp info) the watch page
    is not fetched again; only the caption files are."""
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["yt-dlp", "--skip-download", "--no-progress", "--no-playlist", "--socket-timeout", "20", "--retries", "2"]
    if creator:
        args.append("--write-subs")
    if auto:
        args.append("--write-auto-subs")
    args += ["--sub-langs", sub_langs, "--sub-format", "json3", "--sleep-requests", "1"]
    args += ["-o", str(out_dir / f"{video_id}.%(ext)s")]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    if info_json is not None:
        args += ["--load-info-json", str(info_json)]
    else:
        args += ["--", watch_url(video_id)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, False, "yt-dlp timed out"
    except FileNotFoundError:
        return False, True, "yt-dlp not found on PATH"
    if is_blocked(proc.stderr):
        return False, True, last_error_line(proc.stderr)
    if proc.returncode != 0:
        return False, False, last_error_line(proc.stderr)
    return True, False, ""


@dataclass
class TracksSummary:
    candidates: int = 0
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: str | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)


def fetch_auto_tracks(
    youtube_ids: list[str],
    settings: Settings | None = None,
    language: str | None = None,
    *,
    retry_failed: bool = False,
    cookies_from_browser: str | None = None,
    gap_s: float = 5.0,
    downloader: Callable[..., tuple[bool, bool, str]] = download_tracks,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> TracksSummary:
    """Fetch `<lang>-orig` for each id whose info.json lists it, one at a time, resumable."""
    settings = settings or get_settings()
    lang = language or settings.language
    key = f"{lang}-orig"
    out_dir = tracks_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed_path = out_dir / "failed.json"
    failed: dict = read_json(failed_path, {})
    vako_src = settings.vako_root / ".vr" / "src" / lang

    todo = []
    summary = TracksSummary()
    for vid in dict.fromkeys(youtube_ids):
        if (out_dir / f"{vid}.{key}.json3").exists() or (vako_src / f"{vid}.{key}.json3").exists():
            summary.skipped += 1
            continue
        found = find_info(vid, settings, lang)
        info = load_info(found[0]) if found else None
        if info is None or key not in info.automatic_keys:
            summary.skipped += 1
            continue
        if vid in failed and not retry_failed:
            summary.skipped += 1
            continue
        todo.append(vid)
    summary.candidates = len(todo)
    log(f"{len(todo)} ids need {key}, {summary.skipped} skipped")

    for i, vid in enumerate(todo):
        if i > 0:
            sleep(gap_s + random.uniform(0, 2))
        ok, blocked, err = downloader(
            vid, out_dir, key, creator=False, auto=True, cookies_from_browser=cookies_from_browser
        )
        if blocked:
            summary.stopped = err
            log(f"stopping: {err}")
            break
        if ok and (out_dir / f"{vid}.{key}.json3").exists():
            summary.fetched += 1
            failed.pop(vid, None)
            log(f"[{i + 1}/{len(todo)}] {vid} {key} ok")
        else:
            err = err or f"no {key} file written"
            failed[vid] = {"error": err, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
            summary.failed += 1
            summary.failures.append((vid, err))
            log(f"[{i + 1}/{len(todo)}] {vid} failed: {err}")
        write_json_atomic(failed_path, failed)
    return summary
