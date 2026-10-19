"""Importers that add rows to `datasets/<lang>/clips.csv`.

- `import_vako`: Vako's caption store (`.vr/src/<lang>/`), one importer among others. A video with a
  creator track (classified from info.json) becomes a whole-video row when it lasts 5-90 s, or one
  window row (windows.py) when it is longer. Source `vako`.
- `import_ids`: any CSV of public ids (`video_id` required; `platform`, `start_ms`, `end_ms`,
  `channel_id`, `category`, `license_note` optional). Source `ids` unless given.
- `seed_channels_csv`: a `channels.csv` from what the Vako store knows (channels with creator tracks,
  vouched, beginner lists), for `data discover`.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from clipbench.config import Settings, get_settings
from clipbench.data.datasets import (
    ChannelRow,
    ClipRow,
    channels_path,
    guess_category,
    merge_clips,
    parse_clip_row,
    read_channels,
    read_clips,
    write_channels,
    write_clips,
)
from clipbench.data.info import read_json
from clipbench.data.vako import classify_track, find_info, load_info, scan_tracks
from clipbench.data.windows import choose_window_from_track

MIN_SHORT_S = 5.0
MAX_SHORT_S = 90.0


@dataclass
class ImportSummary:
    scanned: int = 0
    shorts: int = 0
    windows: int = 0
    no_info: int = 0
    no_creator: int = 0
    no_window: int = 0
    too_short: int = 0
    added: int = 0
    already: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def import_vako(language: str, settings: Settings | None = None, *, replace: bool = False) -> ImportSummary:
    """`replace` drops the language's existing `vako` rows first, so a changed window rule takes effect
    (other sources are kept)."""
    settings = settings or get_settings()
    src = settings.vako_root / ".vr" / "src" / language
    summary = ImportSummary()
    rows: list[ClipRow] = []
    for vid, files in scan_tracks(src).items():
        summary.scanned += 1
        found = find_info(vid, settings, language)
        info = load_info(found[0]) if found else None
        if info is None:
            summary.no_info += 1
            continue
        creator = sorted(
            (k for k in files if classify_track(k, info, language) == "creator"),
            key=lambda k: (k != language, k),
        )
        if not creator:
            summary.no_creator += 1
            continue
        dur = info.duration_s
        channel = info.channel_id or ""
        if dur is not None and dur <= MAX_SHORT_S:
            if dur < MIN_SHORT_S:
                summary.too_short += 1
                continue
            rows.append(ClipRow("youtube", vid, channel_id=channel, source="vako"))
            summary.shorts += 1
            continue
        try:
            win = choose_window_from_track(files[creator[0]].read_text(encoding="utf-8"), language, dur)
        except (OSError, ValueError) as exc:
            summary.errors.append(f"{vid}: {exc}")
            continue
        if win is None:
            summary.no_window += 1
            continue
        rows.append(
            ClipRow("youtube", vid, win.start_s * 1000, win.end_s * 1000, channel_id=channel, source="vako")
        )
        summary.windows += 1
    if replace:
        kept = [r for r in read_clips(language, settings) if r.source != "vako"]
        summary.removed = len(read_clips(language, settings)) - len(kept)
        write_clips(language, kept, settings)
    res = merge_clips(language, rows, settings)
    summary.added, summary.already = res.added, res.updated + res.unchanged
    return summary


def import_ids(path: Path, language: str, settings: Settings | None = None, source: str = "ids") -> ImportSummary:
    settings = settings or get_settings()
    summary = ImportSummary()
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for i, raw in enumerate(csv.DictReader(f)):
            summary.scanned += 1
            rows.append(parse_clip_row(raw, default_source=source, line=i + 2))
    res = merge_clips(language, rows, settings)
    summary.added, summary.already = res.added, res.updated + res.unchanged
    return summary


def seed_channels_csv(language: str, settings: Settings | None = None) -> tuple[Path, int, int]:
    """Merge channels known from the Vako store and from clips.csv into channels.csv.

    Category per channel: the most common info.json guess over that channel's clips we have info for
    (marked "category guessed" in the note). Returns (path, total rows, added rows).
    """
    settings = settings or get_settings()
    vr = settings.vako_root / ".vr"
    blocked = set(read_json(vr / "blocked-channels.json", []))
    probed = read_json(vr / f"probed-{language}.json", {})
    wanted: list[str] = []

    def add(cid: object) -> None:
        if isinstance(cid, str) and cid.startswith("UC") and cid not in blocked and cid not in wanted:
            wanted.append(cid)

    for r in read_clips(language, settings):
        add(r.channel_id)
    manual = Counter(
        r.get("channelId") for r in probed.values() if isinstance(r, dict) and r.get("ok") and r.get("manualSubs")
    )
    for cid, _ in manual.most_common():
        add(cid)
    for cid in read_json(vr / "vouched-channels.json", []):
        add(cid)
    for c in read_json(vr / "beginner-channels.json", []):
        if c.get("lang") == language:
            add(c.get("channelId"))

    guesses: dict[str, Counter] = {}
    if settings.info_dir.is_dir() or vr.is_dir():
        for vid, rec in probed.items():
            cid = rec.get("channelId") if isinstance(rec, dict) else None
            if cid not in wanted:
                continue
            found = find_info(vid, settings, language)
            if not found:
                continue
            g = guess_category(read_json(found[0], None))
            if g:
                guesses.setdefault(cid, Counter())[g] += 1

    path = channels_path(language, settings)
    existing = {c.key: c for c in read_channels(path)}
    added = 0
    for cid in wanted:
        if cid in existing:
            continue
        cat = guesses[cid].most_common(1)[0][0] if cid in guesses else ""
        existing[cid] = ChannelRow("youtube", cid, "", cat, "seed; category guessed" if cat else "seed")
        added += 1
    write_channels(path, existing.values())
    return path, len(existing), added
