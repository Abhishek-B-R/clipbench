"""Fetch yt-dlp info.json for videos that Vako has captions for but no metadata.

Gentle by design: one request at a time, at least `gap_s` seconds between requests, resumable
(ids already on disk are skipped), and permanent failures are kept in `data/info/failed.json`
so they are not asked for again unless `retry_failed` is set. A bot check or HTTP 429 stops the
whole run instead of hammering on.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipbench.config import Settings, get_settings
from clipbench.data.json3 import estimate_duration_s
from clipbench.data.vako import find_info, scan_tracks

# Dropped before saving: they are most of the file and nothing here reads them.
HEAVY_FIELDS = ("formats", "thumbnails", "requested_formats", "requested_downloads", "heatmap")

BLOCK_PATTERNS = [
    re.compile(r"confirm you.re not a bot", re.I),
    re.compile(r"HTTP Error 429", re.I),
    re.compile(r"Too Many Requests", re.I),
]
GONE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"Video unavailable",
        r"Private video",
        r"has been removed",
        r"This video is not available",
        r"members-only",
        r"confirm your age",
        r"age-restricted",
        r"account associated with this video has been terminated",
    )
]


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def last_error_line(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    errors = [line for line in lines if line.upper().startswith("ERROR:")]
    return (errors[-1] if errors else (lines[-1] if lines else "no output"))[:300]


def is_blocked(stderr: str) -> bool:
    return any(p.search(stderr) for p in BLOCK_PATTERNS)


def is_gone(stderr: str) -> bool:
    return any(p.search(stderr) for p in GONE_PATTERNS)


def slim_info(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k not in HEAVY_FIELDS}
    # Caption entries carry long signed URLs. One entry per key (name kept, it labels named
    # creator tracks) is enough for classification: a key counts when its list is non empty.
    for name in ("subtitles", "automatic_captions"):
        tracks = out.get(name)
        if isinstance(tracks, dict):
            out[name] = {
                key: [{"name": f.get("name")} for f in fmts[:1] if isinstance(f, dict)]
                for key, fmts in tracks.items()
                if isinstance(fmts, list)
            }
    return out


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@dataclass
class Candidate:
    video_id: str
    estimated_s: float | None


def candidates(settings: Settings, language: str, *, fetch_all: bool, max_estimate_s: float) -> list[Candidate]:
    """Videos with a json3 track and no info.json anywhere, short ones only unless `fetch_all`."""
    src = settings.vako_root / ".vr" / "src" / language
    out: list[Candidate] = []
    for video_id, files in scan_tracks(src).items():
        if find_info(video_id, settings, language) is not None:
            continue
        estimates = []
        for path in files.values():
            try:
                est = estimate_duration_s(path.read_text(encoding="utf-8"))
            except OSError:
                est = None
            if est is not None:
                estimates.append(est)
        estimate = max(estimates) if estimates else None
        if not fetch_all and (estimate is None or estimate > max_estimate_s):
            continue
        out.append(Candidate(video_id, estimate))
    return out


def probed_creator_candidates(settings: Settings, language: str, *, per_channel: int | None) -> list[Candidate]:
    """Videos with no info.json whose Vako track Vako's own probe saw as a creator track.

    Only for targeting: `.vr/probed-<lang>.json` (`manualSubs`) says which videos are worth one
    info.json request. Classification still comes from the info.json fetched here. Ordered round
    robin by the probed channel so `per_channel` keeps requests low and channels spread.
    """
    probed = read_json(settings.vako_root / ".vr" / f"probed-{language}.json", {})
    src = settings.vako_root / ".vr" / "src" / language
    by_channel: dict[str, list[str]] = {}
    for video_id, files in scan_tracks(src).items():
        if find_info(video_id, settings, language) is not None:
            continue
        rec = probed.get(video_id) or {}
        manual = rec.get("manualSubs") or []
        if not rec.get("ok") or not any(key in manual for key in files):
            continue
        by_channel.setdefault(str(rec.get("channelId") or "?"), []).append(video_id)
    out: list[Candidate] = []
    depth = 0
    while True:
        added = False
        for ch in sorted(by_channel):
            ids = sorted(by_channel[ch])
            if depth < len(ids) and (per_channel is None or depth < per_channel):
                out.append(Candidate(ids[depth], None))
                added = True
        if not added:
            return out
        depth += 1


@dataclass
class FetchOutcome:
    ok: bool
    blocked: bool = False
    gone: bool = False
    error: str | None = None
    data: dict[str, Any] | None = None


def run_ytdlp_info(video_id: str, cookies_from_browser: str | None = None, timeout_s: int = 120) -> FetchOutcome:
    args = [
        "yt-dlp",
        "-J",
        "--skip-download",
        "--no-playlist",
        "--no-write-comments",
        "--no-progress",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
    ]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    args += ["--", watch_url(video_id)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return FetchOutcome(ok=False, error="yt-dlp timed out")
    except FileNotFoundError:
        return FetchOutcome(ok=False, blocked=True, error="yt-dlp not found on PATH")
    if is_blocked(proc.stderr):
        return FetchOutcome(ok=False, blocked=True, error=last_error_line(proc.stderr))
    if proc.returncode != 0:
        return FetchOutcome(ok=False, gone=is_gone(proc.stderr), error=last_error_line(proc.stderr))
    try:
        data = json.loads(proc.stdout)
        assert isinstance(data, dict)
    except (json.JSONDecodeError, AssertionError):
        return FetchOutcome(ok=False, error="yt-dlp printed something that is not an info JSON")
    return FetchOutcome(ok=True, data=data)


@dataclass
class FetchSummary:
    candidates: int = 0
    skipped_failed: int = 0
    fetched: int = 0
    failed: int = 0
    stopped: str | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)


def fetch_info(
    settings: Settings | None = None,
    language: str | None = None,
    *,
    fetch_all: bool = False,
    max_estimate_s: float = 95.0,
    retry_failed: bool = False,
    cookies_from_browser: str | None = None,
    limit: int | None = None,
    gap_s: float = 5.0,
    probed_creator: bool = False,
    per_channel: int | None = None,
    runner: Callable[..., FetchOutcome] = run_ytdlp_info,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchSummary:
    settings = settings or get_settings()
    lang = language or settings.language
    info_dir = settings.info_dir
    info_dir.mkdir(parents=True, exist_ok=True)
    failed_path = info_dir / "failed.json"
    failed: dict[str, Any] = read_json(failed_path, {})

    if probed_creator:
        todo = probed_creator_candidates(settings, lang, per_channel=per_channel)
    else:
        todo = candidates(settings, lang, fetch_all=fetch_all, max_estimate_s=max_estimate_s)
    summary = FetchSummary(candidates=len(todo))
    if not retry_failed:
        before = len(todo)
        todo = [c for c in todo if c.video_id not in failed]
        summary.skipped_failed = before - len(todo)
    if limit is not None:
        todo = todo[:limit]
    log(f"{summary.candidates} candidates, {summary.skipped_failed} skipped as failed before, {len(todo)} to fetch")

    for i, cand in enumerate(todo):
        if i > 0:
            sleep(gap_s + random.uniform(0, 2))
        outcome = runner(cand.video_id, cookies_from_browser)
        if outcome.blocked:
            summary.stopped = outcome.error
            log(f"stopping: {outcome.error}")
            break
        if outcome.ok and outcome.data is not None:
            write_json_atomic(info_dir / f"{cand.video_id}.info.json", slim_info(outcome.data))
            failed.pop(cand.video_id, None)
            summary.fetched += 1
            dur = outcome.data.get("duration")
            log(f"[{i + 1}/{len(todo)}] {cand.video_id} ok duration={dur}")
        else:
            failed[cand.video_id] = {
                "error": outcome.error,
                "gone": outcome.gone,
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            summary.failed += 1
            summary.failures.append((cand.video_id, outcome.error or ""))
            log(f"[{i + 1}/{len(todo)}] {cand.video_id} failed: {outcome.error}")
        write_json_atomic(failed_path, failed)
    return summary
