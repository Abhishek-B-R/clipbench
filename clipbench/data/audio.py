"""Audio for the sample: yt-dlp bestaudio, kept as is, plus a 16 kHz mono PCM WAV.

One download at a time, at least `gap_s` seconds between downloads. Resumable: a clip whose
original and WAV exist with hashes recorded is skipped; a clip with an original but no WAV is
only converted. Failures are logged to data/audio/failed.json and do not stop the loop; a bot
check or HTTP 429 does.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from clipbench.config import Settings, get_settings
from clipbench.data.info import is_blocked, last_error_line, read_json, watch_url, write_json_atomic
from clipbench.data.sample import load_sample

SKIP_SUFFIXES = {".wav", ".part", ".ytdl", ".json", ".tmp"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_original(audio_dir: Path, video_id: str) -> Path | None:
    prefix = video_id + "."
    for p in sorted(audio_dir.glob("*")):
        if p.name.startswith(prefix) and p.is_file() and p.suffix not in SKIP_SUFFIXES and p.name.count(".") == 1:
            return p
    return None


def download(
    video_id: str,
    audio_dir: Path,
    cookies_from_browser: str | None = None,
    *,
    youtube_id: str | None = None,
    section: tuple[float, float] | None = None,
) -> tuple[bool, bool, str]:
    """Download to `<audio_dir>/<video_id>.<ext>`. Returns (ok, blocked, error).

    `video_id` is the clip key (a window's clip_id); `youtube_id` the video to fetch (defaults to
    `video_id`). `section` (start_s, end_s) downloads only that part (`--download-sections`).
    """
    args = [
        "yt-dlp",
        "-f",
        "bestaudio",
        "--no-playlist",
        "--no-progress",
        "--no-part",
        "--socket-timeout",
        "20",
        "--retries",
        "3",
        "-o",
        str(audio_dir / f"{video_id}.%(ext)s"),
    ]
    if section is not None:
        args += ["--download-sections", f"*{section[0]:g}-{section[1]:g}"]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    args += ["--", watch_url(youtube_id or video_id)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, False, "yt-dlp timed out"
    except FileNotFoundError:
        return False, True, "yt-dlp not found on PATH"
    if is_blocked(proc.stderr):
        return False, True, last_error_line(proc.stderr)
    if proc.returncode != 0:
        return False, False, last_error_line(proc.stderr)
    return True, False, ""


def to_wav(src: Path, dst: Path, duration_s: float | None = None) -> tuple[bool, str]:
    """16 kHz mono PCM. `duration_s` trims to exactly that length (window clips)."""
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    args = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src)]
    if duration_s is not None:
        args += ["-t", f"{duration_s:g}"]
    args += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False, (proc.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][:300]
    tmp.replace(dst)
    return True, ""


@dataclass
class AudioSummary:
    ready: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: str | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)


def fetch_audio(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    cookies_from_browser: str | None = None,
    gap_s: float = 10.0,
    downloader: Callable[..., tuple[bool, bool, str]] = download,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> AudioSummary:
    settings = settings or get_settings()
    audio_dir = settings.audio_dir
    audio_dir.mkdir(parents=True, exist_ok=True)
    hashes_path = audio_dir / "hashes.json"
    failed_path = audio_dir / "failed.json"
    hashes: dict = read_json(hashes_path, {})
    failed: dict = read_json(failed_path, {})

    sample = load_sample(settings)
    if limit is not None:
        sample = sample.head(limit)
    ids = list(sample["video_id"])
    windows: dict[str, tuple[str, float, float]] = {}
    if "clip_source" in sample:
        for _, r in sample[sample["clip_source"] == "window"].iterrows():
            windows[r["video_id"]] = (r["youtube_id"], float(r["window_start_s"]), float(r["window_end_s"]))
    summary = AudioSummary()
    last_download: float | None = None

    def fail(vid: str, err: str) -> None:
        failed[vid] = {"error": err, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        write_json_atomic(failed_path, failed)
        summary.failed += 1
        summary.failures.append((vid, err))
        log(f"{vid} failed: {err}")

    for i, vid in enumerate(ids):
        wav = audio_dir / f"{vid}.wav"
        original = find_original(audio_dir, vid)
        rec = hashes.get(vid) or {}
        if original and wav.exists() and rec.get("original") and rec.get("wav"):
            summary.skipped += 1
            summary.ready += 1
            continue

        if original is None:
            if last_download is not None:
                wait = gap_s - (time.monotonic() - last_download)
                if wait > 0:
                    sleep(wait + random.uniform(0, 3))
            if vid in windows:
                yt, ws, we = windows[vid]
                ok, blocked, err = downloader(vid, audio_dir, cookies_from_browser, youtube_id=yt, section=(ws, we))
            else:
                ok, blocked, err = downloader(vid, audio_dir, cookies_from_browser)
            last_download = time.monotonic()
            if blocked:
                summary.stopped = err
                log(f"stopping: {err}")
                break
            original = find_original(audio_dir, vid)
            if not ok or original is None:
                fail(vid, err or "no audio file after download")
                continue
            summary.downloaded += 1

        if not wav.exists():
            win = windows.get(vid)
            ok, err = to_wav(original, wav, (win[2] - win[1]) if win else None)
            if not ok:
                fail(vid, f"ffmpeg: {err}")
                continue

        hashes[vid] = {"original": sha256_file(original), "wav": sha256_file(wav)}
        write_json_atomic(hashes_path, hashes)
        if vid in failed:
            failed.pop(vid)
            write_json_atomic(failed_path, failed)
        summary.ready += 1
        log(f"[{i + 1}/{len(ids)}] {vid} ready ({original.name})")
    return summary
