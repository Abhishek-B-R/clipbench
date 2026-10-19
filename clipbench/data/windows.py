"""One 20-60 s window per long video, cut on caption line boundaries.

Rules (see docs/data_rules.md):

- Built from the creator track's lines (`parse_json3_lines`, text as written).
- The window starts at the start of a line and ends at the end of a line, rounded outwards to whole
  seconds (YouTube's `t=` and embeds take whole seconds, as Vako `clipping.ts` does).
- Clean edges only: no other caption line may overlap the rounded window, so the reference is
  exactly the lines inside it and no captioned speech is cut in half.
- Start at or after 10 s (skip intros), end at least 1 s before the info.json duration (the audio
  stream can end a few hundred ms before the duration YouTube reports).
- Length 20-60 s. Among all candidates, the one with the most caption characters per second wins
  (dense speech), ties to the earliest. Windows under 3 characters per second are rejected.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from clipbench.data.json3 import TimedText, parse_json3_lines

MIN_WINDOW_S = 20
MAX_WINDOW_S = 60
SKIP_INTRO_S = 10
MIN_CHARS_PER_S = 3.0
END_MARGIN_S = 1


@dataclass(frozen=True)
class Window:
    start_s: int
    end_s: int
    chars: int

    @property
    def duration_s(self) -> int:
        return self.end_s - self.start_s

    @property
    def density(self) -> float:
        return self.chars / self.duration_s


def clip_id_for(video_id: str, start_s: int, end_s: int) -> str:
    """`<video_id>@<start_ms>-<end_ms>` (docs/V2.md); windows here are whole seconds."""
    return f"{video_id}@{start_s * 1000}-{end_s * 1000}"


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def choose_window(lines: list[TimedText], duration_s: float | None) -> Window | None:
    if not lines:
        return None
    limit_ms = (duration_s - END_MARGIN_S) * 1000 if duration_s else None
    chars = [_chars(t.text) for t in lines]
    best: Window | None = None
    n = len(lines)
    reach_before = 0  # latest end of any line before i
    for i in range(n):
        if i > 0:
            reach_before = max(reach_before, lines[i - 1].end_ms)
        start_s = math.floor(lines[i].start_ms / 1000)
        if start_s < SKIP_INTRO_S:
            continue
        if reach_before > start_s * 1000:  # an earlier line reaches into the rounded start
            continue
        total = 0
        for j in range(i, n):
            total += chars[j]
            end_s = math.ceil(lines[j].end_ms / 1000)
            length = end_s - start_s
            if length > MAX_WINDOW_S:
                break
            if limit_ms is not None and end_s * 1000 > limit_ms:
                break
            if length < MIN_WINDOW_S:
                continue
            if j + 1 < n and lines[j + 1].start_ms < end_s * 1000:
                continue
            cand = Window(start_s, end_s, total)
            if cand.density < MIN_CHARS_PER_S:
                continue
            if best is None or cand.density > best.density:
                best = cand
    return best


def choose_window_from_track(text: str, language: str, duration_s: float | None) -> Window | None:
    return choose_window(parse_json3_lines(text, language), duration_s)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def slice_json3(text: str, start_ms: int, end_ms: int) -> str:
    """The part of a json3 track inside [start_ms, end_ms), rebased to start at 0.

    Works per segment, so a word timed track keeps the words that start inside the window even when
    their event started before it. `aAppend` events (display scrolling) are dropped.
    """
    data = json.loads(text)
    s_ms, e_ms = int(start_ms), int(end_ms)
    out_events: list[dict[str, Any]] = []
    for ev in data.get("events") or []:
        if not isinstance(ev, dict) or ev.get("aAppend") == 1 or not isinstance(ev.get("segs"), list):
            continue
        start = _num(ev.get("tStartMs"))
        if start is None:
            continue
        dur = _num(ev.get("dDurationMs")) or 0.0
        kept: list[tuple[float, dict[str, Any], bool]] = []
        for seg in ev["segs"]:
            if not isinstance(seg, dict):
                continue
            off = _num(seg.get("tOffsetMs"))
            at = start + (off or 0.0)
            if s_ms <= at < e_ms:
                kept.append((at, seg, off is not None))
        if not kept:
            continue
        first = kept[0][0]
        segs = []
        for at, seg, had_offset in kept:
            new = {k: v for k, v in seg.items() if k != "tOffsetMs"}
            if had_offset:  # keeps the track recognisable as timed by the word
                new["tOffsetMs"] = int(round(at - first))
            segs.append(new)
        end = min(start + dur, e_ms)
        new_ev = {k: v for k, v in ev.items() if k not in ("tStartMs", "dDurationMs", "segs")}
        new_ev.update(
            {"tStartMs": int(round(first - s_ms)), "dDurationMs": int(round(max(0.0, end - first))), "segs": segs}
        )
        out_events.append(new_ev)
    head = {k: v for k, v in data.items() if k != "events"}
    head["events"] = out_events
    head["clipbenchWindow"] = {"start_ms": s_ms, "end_ms": e_ms}
    return json.dumps(head, ensure_ascii=False)
