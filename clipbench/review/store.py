"""Storage for the review UI: the queue, verified files, audio hashes and creator captions.

The queue is data/sample.parquet ordered by sample_rank. Clips listed in data/rereview.json
(written by error analysis) come first. Verified files follow the format in docs/INTERFACES.md
and are written atomically (tmp file + rename).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipbench.config import Settings
from clipbench.models import TAGS

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class QueueItem:
    video_id: str
    language: str
    channel: str | None
    duration_s: float | None
    flagged: bool = False


@dataclass
class Queue:
    items: list[QueueItem]
    by_id: dict[str, QueueItem] = field(default_factory=dict)
    flagged_since: float | None = None  # mtime of rereview.json

    def __post_init__(self) -> None:
        self.by_id = {it.video_id: it for it in self.items}

    def index_of(self, video_id: str) -> int:
        for i, it in enumerate(self.items):
            if it.video_id == video_id:
                return i
        raise KeyError(video_id)


def rereview_path(settings: Settings) -> Path:
    return settings.data_dir / "rereview.json"


def load_rereview_ids(settings: Settings) -> list[str]:
    path = rereview_path(settings)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):  # tolerate {"video_ids": [...]}
        raw = raw.get("video_ids", [])
    return [str(v) for v in raw if isinstance(v, str)]


def _clean(value: Any) -> Any:
    """Turn pandas NA/NaN into None."""
    try:
        import pandas as pd

        if value is None or (not isinstance(value, (list, dict, str)) and pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def load_queue(settings: Settings, only_rereview: bool = False) -> Queue:
    import pandas as pd

    if not settings.sample_path.exists():
        return Queue(items=[])
    df = pd.read_parquet(settings.sample_path)
    if "sample_rank" in df.columns:
        df = df.sort_values("sample_rank", kind="stable")
    items: list[QueueItem] = []
    for row in df.to_dict("records"):
        vid = str(row["video_id"])
        if not VIDEO_ID_RE.match(vid):
            continue
        dur = _clean(row.get("duration_s"))
        items.append(
            QueueItem(
                video_id=vid,
                language=_clean(row.get("language")) or settings.language,
                channel=_clean(row.get("channel")),
                duration_s=float(dur) if dur is not None else None,
            )
        )
    flagged = set(load_rereview_ids(settings))
    for it in items:
        it.flagged = it.video_id in flagged
    first = [it for it in items if it.flagged]
    rest = [] if only_rereview else [it for it in items if not it.flagged]
    path = rereview_path(settings)
    since = path.stat().st_mtime if flagged and path.exists() else None
    return Queue(items=first + rest, flagged_since=since)


def verified_path(settings: Settings, video_id: str) -> Path:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"bad video id: {video_id!r}")
    return settings.verified_dir / f"{video_id}.json"


def read_verified(settings: Settings, video_id: str) -> dict | None:
    path = verified_path(settings, video_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip_status(settings: Settings, queue: Queue, item: QueueItem) -> str:
    """One of "todo", "reviewed", "skipped", "rereview".

    A flagged clip counts as done only once it was saved after rereview.json was written.
    """
    rec = read_verified(settings, item.video_id)
    if rec is None:
        return "rereview" if item.flagged else "todo"
    if item.flagged and queue.flagged_since is not None:
        ts = _parse_ts(rec.get("reviewed_at"))
        # reviewed_at has whole seconds, so compare against the truncated mtime.
        if ts is None or ts < int(queue.flagged_since):
            return "rereview"
    return "skipped" if rec.get("skipped") else "reviewed"


def audio_path(settings: Settings, video_id: str) -> Path:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"bad video id: {video_id!r}")
    return settings.audio_dir / f"{video_id}.wav"


def audio_sha256(settings: Settings, video_id: str) -> str | None:
    """sha256 of the WAV from hashes.json, computed from the file when missing there."""
    hashes = settings.audio_dir / "hashes.json"
    if hashes.exists():
        try:
            entry = json.loads(hashes.read_text(encoding="utf-8")).get(video_id)
            if isinstance(entry, dict) and entry.get("wav"):
                return str(entry["wav"])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    path = audio_path(settings, video_id)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_creator(video_id: str, settings: Settings) -> tuple[str, list[dict] | None]:
    """Creator caption text and words. Imported lazily so tests can swap the module."""
    from clipbench.data.references import load_reference

    ref = load_reference(video_id, "creator", settings)
    if ref is None:
        return "", None
    words = [w.model_dump() if hasattr(w, "model_dump") else dict(w) for w in (ref.words or [])] or None
    return ref.text or "", words


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clean_tags(tags: list[str]) -> list[str]:
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise ValueError(f"unknown tags: {unknown}")
    return [t for t in TAGS if t in set(tags)]  # canonical order, no duplicates


def build_record(
    settings: Settings,
    item: QueueItem,
    *,
    text: str,
    original_text: str,
    tags: list[str],
    reviewer: str,
    skipped: bool,
) -> dict:
    return {
        "video_id": item.video_id,
        "language": item.language,
        "text": text,
        "original_text": original_text,
        "tags": clean_tags(tags),
        "reviewer": reviewer,
        "reviewed_at": utc_now_iso(),
        "audio_sha256": audio_sha256(settings, item.video_id),
        "skipped": skipped,
    }


def save_verified(settings: Settings, record: dict) -> Path:
    path = verified_path(settings, record["video_id"])
    write_json_atomic(path, record)
    return path
