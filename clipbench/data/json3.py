"""YouTube json3 caption tracks.

A port of Vako `apps/api/src/captions/youtube.ts` (`parseJson3`, `parseJson3Words`, `tidy`,
`SOUND_TAG`, `withoutSoundTags`). Ported, not imported: Vako is TypeScript and read only.

json3 describes a display, not a transcript. Events with `aAppend == 1` only scroll the window
and are skipped. Each text event is cut at the start of the next one, because ASR lines stay on
screen until the line after next. A newline inside an event is a display break: a space in a
spaced language, nothing in ja/zh.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from clipbench.models import Word

MAX_INT4 = 2_147_483_647

# ASR marks non speech in square brackets: [Music], [Applause], [音楽]. Same pattern as Vako.
SOUND_TAG = re.compile(r"\[[^\]\n]{1,40}\]")

UNSPACED_LANGUAGES = {"ja", "zh"}


@dataclass
class TimedText:
    start_ms: int
    end_ms: int
    text: str


class Json3Error(ValueError):
    """The track is not JSON or has no events."""


def is_unspaced(language: str) -> bool:
    return language.lower().split("-")[0] in UNSPACED_LANGUAGES


def _to_int(value: float) -> int:
    return max(0, min(MAX_INT4, round(value)))


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def tidy(text: str, language: str) -> str:
    """Collapse display whitespace. A newline goes the way a space would in this language."""
    if is_unspaced(language):
        # A line break between two Latin words (English inside a Japanese caption) is still a word gap;
        # between CJK characters it is only a display break.
        text = re.sub(r"(?<=[A-Za-z0-9.,!?'])\r?\n(?=[A-Za-z0-9])", " ", text)
    unbroken = re.sub(r"\r?\n", "" if is_unspaced(language) else " ", text)
    return re.sub(r"[ \t]+", " ", unbroken).strip()


def _events(text: str, *, strict: bool) -> list[Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        if strict:
            raise Json3Error("caption track is not JSON") from exc
        return []
    events = parsed.get("events") if isinstance(parsed, dict) else None
    if not isinstance(events, list):
        if strict:
            raise Json3Error("caption track has no events")
        return []
    return events


def without_sound_tags(lines: list[TimedText]) -> list[TimedText]:
    kept: list[TimedText] = []
    for line in lines:
        text = re.sub(r"[ \t]+", " ", SOUND_TAG.sub(" ", line.text)).strip()
        if text:
            kept.append(TimedText(line.start_ms, line.end_ms, text))
    return kept


def parse_json3_lines(text: str, language: str, *, strip_sound_tags: bool = False) -> list[TimedText]:
    """Timed lines of a json3 track (Vako `parseJson3`).

    `strip_sound_tags` should be True for ASR tracks only. Creator captions are left as written,
    as Vako does.
    """
    lines: list[TimedText] = []
    for event in _events(text, strict=True):
        if not isinstance(event, dict) or event.get("aAppend") == 1 or not isinstance(event.get("segs"), list):
            continue
        start = _num(event.get("tStartMs"))
        if start is None:
            continue
        duration = _num(event.get("dDurationMs")) or 0.0
        joined = "".join(
            seg["utf8"] for seg in event["segs"] if isinstance(seg, dict) and isinstance(seg.get("utf8"), str)
        )
        line = tidy(joined, language)
        if not line:
            continue
        lines.append(TimedText(_to_int(start), _to_int(start + max(0.0, duration)), line))

    lines.sort(key=lambda t: (t.start_ms, t.end_ms))
    for current, nxt in zip(lines, lines[1:], strict=False):
        if nxt.start_ms > current.start_ms and current.end_ms > nxt.start_ms:
            current.end_ms = nxt.start_ms
    return without_sound_tags(lines) if strip_sound_tags else lines


def join_lines(lines: list[TimedText], language: str) -> str:
    return ("" if is_unspaced(language) else " ").join(line.text for line in lines)


def parse_json3_text(text: str, language: str, *, strip_sound_tags: bool = False) -> str:
    """The whole track as one string: lines joined with '' for ja/zh and ' ' otherwise."""
    return join_lines(parse_json3_lines(text, language, strip_sound_tags=strip_sound_tags), language)


def parse_json3_words(text: str) -> list[Word]:
    """Words of a recognition track with start times (Vako `parseJson3Words`).

    Only a track timed by the word (segments carrying `tOffsetMs`) has these. A creator track has
    one piece per line and no offsets and returns []. Sound tags are dropped.
    """
    words: list[Word] = []
    timed_by_word = False
    for event in _events(text, strict=False):
        if not isinstance(event, dict) or event.get("aAppend") == 1 or not isinstance(event.get("segs"), list):
            continue
        start = _num(event.get("tStartMs"))
        if start is None:
            continue
        duration = _num(event.get("dDurationMs"))
        event_end = _to_int(start + duration) if duration is not None and duration > 0 else None
        for seg in event["segs"]:
            if not isinstance(seg, dict) or not isinstance(seg.get("utf8"), str):
                continue
            offset = _num(seg.get("tOffsetMs"))
            if offset is not None:
                timed_by_word = True
            word = SOUND_TAG.sub(" ", seg["utf8"]).strip()
            if not word:
                continue
            word_start = _to_int(start + (offset or 0.0))
            end = event_end if event_end is not None and event_end > word_start else word_start
            words.append(Word(text=word, start_ms=word_start, end_ms=end))
    if not timed_by_word:
        return []

    words.sort(key=lambda w: w.start_ms)
    for i, word in enumerate(words[:-1]):
        nxt = words[i + 1].start_ms
        if word.end_ms <= word.start_ms or nxt < word.end_ms:
            word.end_ms = nxt
    return words


def estimate_duration_s(text: str) -> float | None:
    """Rough clip length from a track: the end of the last event. A lower bound on the real length."""
    end = 0.0
    found = False
    for event in _events(text, strict=False):
        if not isinstance(event, dict):
            continue
        start = _num(event.get("tStartMs"))
        if start is None:
            continue
        found = True
        end = max(end, start + (_num(event.get("dDurationMs")) or 0.0))
    return end / 1000.0 if found else None
