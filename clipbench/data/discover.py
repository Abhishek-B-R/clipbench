"""Find more Shorts with a creator caption track in the language (`clipbench data discover`).

Seed channels come from Vako's `.vr` files (read only), best bets first:

1. channels where Vako's probe saw creator captions (`probed-<lang>.json`, `manualSubs`), by count;
2. `vouched-channels.json`;
3. `beginner-channels.json` entries for the language, by `easy`;
4. `channels-db.json` entries for the language, by `live` then `easy`.

`blocked-channels.json` channels are skipped. For each channel: one `--flat-playlist` request lists
its Shorts tab, then one info.json request per Short not already known (in Vako, in data/info, or in
Vako's probe records). A Short is kept when its info.json has a `subtitles` key in the language (not
`-orig`), says it is spoken in the language (info `language`, or a `<lang>-orig` ASR track), and lasts
5-90 s. For kept Shorts the creator track(s) and `<lang>-orig` are downloaded into data/tracks/.

Gentle: one yt-dlp process at a time, `gap_s` + jitter between them, a bot check or 429 stops the run,
`max_requests` caps yt-dlp invocations per run. A channel is dropped after `miss_limit` Shorts in a
row without creator captions (while it has none kept), and finished at `per_channel` kept Shorts.
Everything is resumable: listings, info files and per channel state are on disk.
"""

from __future__ import annotations

import json
import random
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipbench.config import Settings, get_settings
from clipbench.data.info import (
    FetchOutcome,
    is_blocked,
    last_error_line,
    read_json,
    run_ytdlp_info,
    slim_info,
    write_json_atomic,
)
from clipbench.data.tracks import download_tracks
from clipbench.data.vako import VIDEO_ID, base_of, is_original, scan_tracks, summarize_info, tracks_dir


def seed_channels(settings: Settings, language: str) -> list[str]:
    vr = settings.vako_root / ".vr"
    blocked = set(read_json(vr / "blocked-channels.json", []))
    order: list[str] = []

    def add(cid: Any) -> None:
        if isinstance(cid, str) and cid.startswith("UC") and cid not in blocked and cid not in order:
            order.append(cid)

    probed = read_json(vr / f"probed-{language}.json", {})
    manual = Counter(
        r.get("channelId") for r in probed.values() if isinstance(r, dict) and r.get("ok") and r.get("manualSubs")
    )
    for cid, _ in sorted(manual.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        add(cid)
    for cid in read_json(vr / "vouched-channels.json", []):
        add(cid)
    beginners = [c for c in read_json(vr / "beginner-channels.json", []) if c.get("lang") == language]
    for c in sorted(beginners, key=lambda c: (-(c.get("easy") or 0), c.get("channelId") or "")):
        add(c.get("channelId"))
    db = [c for c in read_json(vr / "channels-db.json", []) if c.get("language") == language]
    for c in sorted(db, key=lambda c: (-(c.get("live") or 0), -(c.get("easy") or 0), c.get("channelId") or "")):
        add(c.get("channelId"))
    return order


def known_ids(settings: Settings, language: str) -> set[str]:
    known = set(scan_tracks(settings.vako_root / ".vr" / "src" / language))
    known |= set(read_json(settings.vako_root / ".vr" / f"probed-{language}.json", {}))
    if settings.info_dir.is_dir():
        known |= {p.name[:11] for p in settings.info_dir.glob("*.info.json")}
    known |= set(read_json(settings.info_dir / "failed.json", {}))
    return known


def list_shorts(channel_id: str, cookies_from_browser: str | None = None) -> tuple[list[str] | None, bool, str]:
    """Returns (ids or None, blocked, error)."""
    args = ["yt-dlp", "--flat-playlist", "-J", "--no-warnings", "--socket-timeout", "20", "--playlist-end", "100"]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    args += ["--", f"https://www.youtube.com/channel/{channel_id}/shorts"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None, False, "yt-dlp timed out"
    if is_blocked(proc.stderr):
        return None, True, last_error_line(proc.stderr)
    if proc.returncode != 0:
        return None, False, last_error_line(proc.stderr)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, False, "listing is not JSON"
    ids = [e.get("id") for e in data.get("entries") or [] if isinstance(e, dict)]
    return [i for i in ids if isinstance(i, str) and VIDEO_ID.match(i)], False, ""


def keep_reason(data: dict[str, Any], language: str, blocked: set[str]) -> str | None:
    """None when the Short is kept, otherwise why not."""
    info = summarize_info(data)
    if info.channel_id in blocked:
        return "blocked channel"
    creator = [k for k in info.subtitle_keys if base_of(k) == language and not is_original(k)]
    if not creator:
        return "no creator track"
    spoken = (info.language and base_of(info.language) == language) or f"{language}-orig" in info.automatic_keys
    if not spoken:
        return f"not spoken in {language} (language {info.language})"
    if info.duration_s is None or not 5 <= info.duration_s <= 90:
        return f"duration {info.duration_s}"
    return None


@dataclass
class DiscoverSummary:
    requests: int = 0
    channels_listed: int = 0
    inspected: int = 0
    kept: int = 0
    stopped: str | None = None


def discover(
    settings: Settings | None = None,
    language: str | None = None,
    *,
    max_requests: int = 1500,
    per_channel: int = 15,
    miss_limit: int = 6,
    gap_s: float = 5.0,
    cookies_from_browser: str | None = None,
    lister: Callable[..., tuple[list[str] | None, bool, str]] = list_shorts,
    info_runner: Callable[..., FetchOutcome] = run_ytdlp_info,
    downloader: Callable[..., tuple[bool, bool, str]] = download_tracks,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> DiscoverSummary:
    settings = settings or get_settings()
    lang = language or settings.language
    root = settings.data_dir / "discover"
    state_path = root / f"state-{lang}.json"
    state: dict[str, Any] = read_json(state_path, {"channels": {}})
    blocked = set(read_json(settings.vako_root / ".vr" / "blocked-channels.json", []))
    known = known_ids(settings, lang)
    out_tracks = tracks_dir(settings)
    settings.info_dir.mkdir(parents=True, exist_ok=True)
    summary = DiscoverSummary()
    first_call = True

    def pace() -> None:
        nonlocal first_call
        if not first_call:
            sleep(gap_s + random.uniform(0, 2))
        first_call = False
        summary.requests += 1

    def save() -> None:
        write_json_atomic(state_path, state)

    channels = seed_channels(settings, lang)
    log(f"{len(channels)} seed channels, {len(known)} ids already known, budget {max_requests} requests")
    for cid in channels:
        ch = state["channels"].setdefault(cid, {"kept": 0, "inspected": 0, "misses": 0, "done": False})
        if ch.get("done"):
            continue
        listing_path = root / "channels" / f"{cid}.json"
        ids = read_json(listing_path, None)
        if ids is None:
            if summary.requests >= max_requests:
                summary.stopped = "max requests"
                break
            pace()
            ids, blocked_now, err = lister(cid, cookies_from_browser)
            if blocked_now:
                summary.stopped = err
                break
            if ids is None:
                ch.update(done=True, reason=f"listing failed: {err}")
                save()
                log(f"{cid}: listing failed: {err}")
                continue
            write_json_atomic(listing_path, ids)
            summary.channels_listed += 1
        for vid in ids:
            if ch["kept"] >= per_channel:
                ch.update(done=True, reason="per channel cap")
                break
            if ch["kept"] == 0 and ch["misses"] >= miss_limit:
                ch.update(done=True, reason="no creator captions")
                break
            if ch["misses"] >= 3 * miss_limit:
                ch.update(done=True, reason="creator captions ran out")
                break
            if vid in known:
                continue
            if summary.requests >= max_requests:
                summary.stopped = "max requests"
                break
            pace()
            outcome = info_runner(vid, cookies_from_browser)
            if outcome.blocked:
                summary.stopped = outcome.error
                break
            known.add(vid)
            ch["inspected"] += 1
            summary.inspected += 1
            if not outcome.ok or outcome.data is None:
                ch["misses"] += 1
                failed = read_json(settings.info_dir / "failed.json", {})
                failed[vid] = {"error": outcome.error, "gone": outcome.gone, "via": "discover"}
                write_json_atomic(settings.info_dir / "failed.json", failed)
                continue
            write_json_atomic(settings.info_dir / f"{vid}.info.json", slim_info(outcome.data))
            reason = keep_reason(outcome.data, lang, blocked)
            if reason is not None:
                ch["misses"] += 1
                continue
            if summary.requests >= max_requests:
                summary.stopped = "max requests"
                break
            pace()
            with tempfile.TemporaryDirectory() as tmp:
                full = Path(tmp) / f"{vid}.info.json"
                full.write_text(json.dumps(outcome.data), encoding="utf-8")
                ok, blocked_now, err = downloader(
                    vid,
                    out_tracks,
                    f"{lang},{lang}-orig,{lang}-.*",
                    creator=True,
                    auto=True,
                    info_json=full,
                    cookies_from_browser=cookies_from_browser,
                )
            if blocked_now:
                summary.stopped = err
                break
            if not ok:
                log(f"{vid}: tracks failed: {err}")
                ch["misses"] += 1
                continue
            ch["kept"] += 1
            ch["misses"] = 0
            summary.kept += 1
            log(f"{cid} {vid}: kept ({ch['kept']} in channel, {summary.kept} this run, {summary.requests} requests)")
            save()
        else:
            ch.update(done=True, reason=ch.get("reason") or "listing exhausted")
        save()
        if summary.stopped:
            break
        log(f"{cid}: {ch['kept']} kept of {ch['inspected']} inspected ({ch.get('reason', 'in progress')})")
    save()
    if summary.stopped:
        log(f"stopped: {summary.stopped}")
    return summary
