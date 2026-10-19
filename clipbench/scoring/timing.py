"""Word timing metrics: caption timing error and hallucination over non-speech.

Both work on word timings (`Word.start_ms`, `Word.end_ms`). Reference timings come from the creator
track or from `clipbench align`; hypothesis timings from the provider. See METHODOLOGY.md.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from clipbench.models import NonSpeechSpan, Word
from clipbench.scoring.metrics import align

TIMING_TOLERANCE_MS = 200
NONSPEECH_GAP_MS = 1000

Tokenizer = Callable[[str], list[str]]


# ---------------------------------------------------------------------------
# Caption timing error
# ---------------------------------------------------------------------------


@dataclass
class TimedTokens:
    tokens: list[str]
    starts: list[float]  # estimated start of each token
    word_index: list[int]  # which word each token came from
    first_token: dict[int, int]  # word index -> index of its first token


def timed_tokens(words: Sequence[Word], tokenizer: Tokenizer) -> TimedTokens:
    """Tokenise each word on its own and give every token a start time.

    A word that yields k tokens spreads them evenly over its duration: token j starts at
    start + j / k * (end - start). One token per word gets the word start exactly.
    """
    out = TimedTokens([], [], [], {})
    for i, w in enumerate(words):
        toks = tokenizer(w.text)
        if not toks:
            continue
        out.first_token[i] = len(out.tokens)
        span = max(w.end_ms - w.start_ms, 0)
        for j, tok in enumerate(toks):
            out.tokens.append(tok)
            out.starts.append(w.start_ms + span * j / len(toks))
            out.word_index.append(i)
    return out


@dataclass
class TimingError:
    n_words: int
    median_ms: float | None
    p90_ms: float | None
    within_200ms: float | None
    offsets_ms: list[float]


def timing_error(
    ref_words: Sequence[Word] | None,
    hyp_words: Sequence[Word] | None,
    tokenizer: Tokenizer,
    *,
    tolerance_ms: int = TIMING_TOLERANCE_MS,
) -> TimingError | None:
    """|predicted start - reference start| over reference words the provider got right.

    Both sides are tokenised per word with the primary metric tokenizer and aligned. A reference word
    is matched when its first token is a hit; its predicted start is the estimated start of the hit
    hypothesis token. None when either side has no word timings.
    """
    if not ref_words or not hyp_words:
        return None
    ref = timed_tokens(ref_words, tokenizer)
    hyp = timed_tokens(hyp_words, tokenizer)
    a = align(ref.tokens, hyp.tokens)
    hit_at = {o.ref_index: o.hyp_index for o in a.ops if o.op == "equal"}
    offsets = []
    for w_index, t_index in ref.first_token.items():
        h_index = hit_at.get(t_index)
        if h_index is not None:
            offsets.append(abs(hyp.starts[h_index] - ref_words[w_index].start_ms))
    if not offsets:
        return TimingError(0, None, None, None, [])
    arr = np.asarray(offsets)
    return TimingError(
        n_words=len(offsets),
        median_ms=float(np.median(arr)),
        p90_ms=float(np.percentile(arr, 90)),
        within_200ms=float(np.mean(arr <= tolerance_ms)),
        offsets_ms=offsets,
    )


# ---------------------------------------------------------------------------
# Non-speech
# ---------------------------------------------------------------------------


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def nonspeech_intervals(
    spans: Sequence[NonSpeechSpan] | None,
    ref_words: Sequence[Word] | None,
    duration_ms: float | None,
    *,
    gap_ms: int = NONSPEECH_GAP_MS,
) -> list[tuple[float, float]] | None:
    """Stretches of the clip with no reference speech.

    Tagged `nonspeech_spans` win when there are any. Otherwise: gaps longer than `gap_ms` between
    reference words, plus the lead in before the first word and the tail after the last word when
    those are longer than `gap_ms` (the tail needs the clip duration). None when neither is known.
    """
    if spans:
        return _merge([(float(s.start_ms), float(s.end_ms)) for s in spans])
    if not ref_words:
        return None
    words = sorted(ref_words, key=lambda w: w.start_ms)
    out: list[tuple[float, float]] = []
    if words[0].start_ms > gap_ms:
        out.append((0.0, float(words[0].start_ms)))
    covered = words[0].end_ms
    for w in words[1:]:
        if w.start_ms - covered > gap_ms:
            out.append((float(covered), float(w.start_ms)))
        covered = max(covered, w.end_ms)
    if duration_ms is not None and duration_ms - covered > gap_ms:
        out.append((float(covered), float(duration_ms)))
    return _merge(out)


@dataclass
class NonSpeechHallucination:
    nonspeech_minutes: float | None
    words: int | None
    per_minute: float | None


def nonspeech_hallucination(
    intervals: list[tuple[float, float]] | None, hyp_words: Sequence[Word] | None
) -> NonSpeechHallucination:
    """Hypothesis words whose midpoint lies inside non-speech, and that count per non-speech minute."""
    if intervals is None:
        return NonSpeechHallucination(None, None, None)
    minutes = sum(end - start for start, end in intervals) / 60000
    if not hyp_words:
        return NonSpeechHallucination(minutes, None, None)
    count = 0
    for w in hyp_words:
        mid = (w.start_ms + w.end_ms) / 2
        if any(start <= mid < end for start, end in intervals):
            count += 1
    return NonSpeechHallucination(minutes, count, count / minutes if minutes > 0 else None)
