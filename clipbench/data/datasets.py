"""The public dataset spec: `datasets/<lang>/clips.csv` and `datasets/<lang>/channels.csv`.

clips.csv is the source of truth for which clips exist. It holds ids and windows only, never caption
text, so it can be committed. Columns (docs/V2.md "Dataset spec"):

    platform, video_id, start_ms, end_ms, channel_id, category, license_note, source

`start_ms`/`end_ms` are both blank for a whole video. `clip_id` is `<video_id>` for a whole video and
`<video_id>@<start_ms>-<end_ms>` for a window; it is what the rest of the code calls `video_id`.

channels.csv (input for `data discover`): `platform, channel_id, handle, category, note`.

The datasets root is `<repo>/datasets`, or `CLIPBENCH_DATASETS_DIR` when set (tests).
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipbench.config import Settings, get_settings
from clipbench.models import CATEGORIES

PLATFORMS = ("youtube", "tiktok", "instagram")
CLIP_COLUMNS = ["platform", "video_id", "start_ms", "end_ms", "channel_id", "category", "license_note", "source"]
CHANNEL_COLUMNS = ["platform", "channel_id", "handle", "category", "note"]
DEFAULT_LICENSE_NOTE = "public video; audio not redistributed"


def datasets_root(settings: Settings | None = None) -> Path:
    env = os.environ.get("CLIPBENCH_DATASETS_DIR")
    if env:
        return Path(env)
    return (settings or get_settings()).root / "datasets"


def clips_path(language: str, settings: Settings | None = None) -> Path:
    return datasets_root(settings) / language / "clips.csv"


def channels_path(language: str, settings: Settings | None = None) -> Path:
    return datasets_root(settings) / language / "channels.csv"


def clip_id_of(video_id: str, start_ms: int | None, end_ms: int | None) -> str:
    if start_ms is None or end_ms is None:
        return video_id
    return f"{video_id}@{int(start_ms)}-{int(end_ms)}"


def split_clip_id(clip_id: str) -> tuple[str, int | None, int | None]:
    m = re.fullmatch(r"(.+)@(\d+)-(\d+)", clip_id)
    if not m:
        return clip_id, None, None
    return m.group(1), int(m.group(2)), int(m.group(3))


def source_url(platform: str, video_id: str) -> str:
    if platform == "tiktok":
        return f"https://www.tiktok.com/@/video/{video_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/reel/{video_id}/"
    return f"https://www.youtube.com/watch?v={video_id}"


def public_url(platform: str, video_id: str, start_ms: int | None = None) -> str:
    """The link a person clicks: Shorts link for a whole YouTube video, watch link with t= for a window."""
    if platform == "youtube":
        if start_ms is not None:
            return f"https://www.youtube.com/watch?v={video_id}&t={int(start_ms) // 1000}s"
        return f"https://www.youtube.com/shorts/{video_id}"
    return source_url(platform, video_id)


@dataclass
class ClipRow:
    platform: str
    video_id: str
    start_ms: int | None = None
    end_ms: int | None = None
    channel_id: str = ""
    category: str = ""
    license_note: str = DEFAULT_LICENSE_NOTE
    source: str = ""

    @property
    def clip_id(self) -> str:
        return clip_id_of(self.video_id, self.start_ms, self.end_ms)

    def as_csv(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "video_id": self.video_id,
            "start_ms": "" if self.start_ms is None else int(self.start_ms),
            "end_ms": "" if self.end_ms is None else int(self.end_ms),
            "channel_id": self.channel_id or "",
            "category": self.category or "",
            "license_note": self.license_note or "",
            "source": self.source or "",
        }


class SpecError(ValueError):
    pass


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    return int(float(s))


def parse_clip_row(raw: dict[str, Any], *, default_source: str = "", line: int | None = None) -> ClipRow:
    where = f" (line {line})" if line else ""
    platform = (raw.get("platform") or "youtube").strip().lower()
    if platform not in PLATFORMS:
        raise SpecError(f"unknown platform {platform!r}{where}")
    vid = (raw.get("video_id") or "").strip()
    if not vid:
        raise SpecError(f"missing video_id{where}")
    start, end = _int_or_none(raw.get("start_ms")), _int_or_none(raw.get("end_ms"))
    if (start is None) != (end is None):
        raise SpecError(f"start_ms and end_ms must both be set or both blank{where}")
    if start is not None and end is not None and not 0 <= start < end:
        raise SpecError(f"bad window {start}-{end}{where}")
    category = (raw.get("category") or "").strip()
    if category and category not in CATEGORIES:
        raise SpecError(f"unknown category {category!r}{where}; use one of {', '.join(CATEGORIES)}")
    return ClipRow(
        platform=platform,
        video_id=vid,
        start_ms=start,
        end_ms=end,
        channel_id=(raw.get("channel_id") or "").strip(),
        category=category,
        license_note=(raw.get("license_note") or DEFAULT_LICENSE_NOTE).strip(),
        source=(raw.get("source") or default_source).strip(),
    )


def read_clips(language: str, settings: Settings | None = None) -> list[ClipRow]:
    path = clips_path(language, settings)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [parse_clip_row(r, line=i + 2) for i, r in enumerate(csv.DictReader(f))]


def write_clips(language: str, rows: Iterable[ClipRow], settings: Settings | None = None) -> Path:
    path = clips_path(language, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r.platform, r.video_id, r.start_ms or -1))
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CLIP_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r.as_csv())
    tmp.replace(path)
    return path


@dataclass
class MergeResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    added_ids: list[str] = field(default_factory=list)


def merge_clips(language: str, new: Iterable[ClipRow], settings: Settings | None = None) -> MergeResult:
    """Add rows to clips.csv, keyed by (platform, clip_id). An existing row keeps its values; blank
    channel_id/category are filled from the new row. Rows are never removed here."""
    existing = {(r.platform, r.clip_id): r for r in read_clips(language, settings)}
    res = MergeResult()
    for row in new:
        key = (row.platform, row.clip_id)
        old = existing.get(key)
        if old is None:
            existing[key] = row
            res.added += 1
            res.added_ids.append(row.clip_id)
            continue
        changed = False
        for attr in ("channel_id", "category"):
            if not getattr(old, attr) and getattr(row, attr):
                setattr(old, attr, getattr(row, attr))
                changed = True
        res.updated += int(changed)
        res.unchanged += int(not changed)
    write_clips(language, existing.values(), settings)
    return res


@dataclass
class ChannelRow:
    platform: str
    channel_id: str
    handle: str
    category: str
    note: str

    @property
    def key(self) -> str:
        return self.channel_id or self.handle

    def shorts_url(self) -> str | None:
        if self.platform != "youtube":
            return None
        if self.channel_id.startswith("UC"):
            return f"https://www.youtube.com/channel/{self.channel_id}/shorts"
        if self.handle:
            h = self.handle if self.handle.startswith("@") else "@" + self.handle
            return f"https://www.youtube.com/{h}/shorts"
        return None


def read_channels(path: Path) -> list[ChannelRow]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            out.append(
                ChannelRow(
                    platform=(r.get("platform") or "youtube").strip().lower(),
                    channel_id=(r.get("channel_id") or "").strip(),
                    handle=(r.get("handle") or "").strip(),
                    category=(r.get("category") or "").strip(),
                    note=(r.get("note") or "").strip(),
                )
            )
        bad = [c.key for c in out if any(ch.isspace() for ch in c.channel_id + c.handle)]
        if bad:
            raise SpecError(f"{path}: whitespace inside channel_id/handle: {', '.join(bad[:5])}")
        return [c for c in out if c.key]


def write_channels(path: Path, rows: Iterable[ChannelRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHANNEL_COLUMNS, lineterminator="\n")
        w.writeheader()
        for c in rows:
            w.writerow({k: getattr(c, k) for k in CHANNEL_COLUMNS})


# ---------------------------------------------------------------------------
# Category guess (a pre-fill for the review UI, never authoritative)
# ---------------------------------------------------------------------------

_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("cooking_tutorial", re.compile(r"recipe|cooking|cook |how to|tutorial|diy|料理|レシピ|作り方|요리|레시피|receta|cocina|रेसिपी|खाना", re.I)),
    ("gaming", re.compile(r"gaming|gameplay|minecraft|fortnite|roblox|valorant|\bgta\b|ゲーム|実況|게임|videojuego|गेमिंग", re.I)),
    ("news_explainer", re.compile(r"news|explained|explainer|breaking|ニュース|解説|뉴스|noticias|समाचार|खबर", re.I)),
    ("comedy_skit", re.compile(r"comedy|skit|prank|funny|sketch|parody|コント|ドッキリ|コメディ|개그|코미디|몰카|comedia|broma|कॉमेडी", re.I)),
    ("vlog_music", re.compile(r"vlog|day in my life|travel|ルーティン|브이로그|일상|viaje|व्लॉग", re.I)),
]
_YT_CATEGORIES = {
    "Gaming": "gaming",
    "Comedy": "comedy_skit",
    "News & Politics": "news_explainer",
    "Education": "news_explainer",
    "Science & Technology": "news_explainer",
    "Howto & Style": "cooking_tutorial",
    "Travel & Events": "vlog_music",
    "Music": "vlog_music",
    "People & Blogs": "talking_head",
}


def guess_category(info: dict[str, Any] | None) -> str | None:
    """Cheap guess from info.json: title/tags keywords first, then YouTube's category. None if no info."""
    if not info:
        return None
    text = " ".join([str(info.get("title") or "")] + [str(t) for t in info.get("tags") or []])
    for cat, pat in _KEYWORDS:
        if pat.search(text):
            return cat
    for yt in info.get("categories") or []:
        if yt in _YT_CATEGORIES:
            return _YT_CATEGORIES[yt]
    return "talking_head" if info.get("categories") else None
