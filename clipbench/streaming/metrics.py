"""Per clip streaming metrics. Every definition here is written out in METHODOLOGY terms so it can be checked
by hand (tests/test_streaming_metrics.py does exactly that).

Times are wall milliseconds on the pacer's clock.

- `ttfp_ms`, time to first partial: from the first audio byte sent to the first event with text (a partial,
  or a final when the provider sends no partial before it).
- `ttfs_ms`, time to final segment: from the end of the audio (when it would have finished playing) to the
  last final. 0 when the last final arrived before the audio ended.
- Segments: events are grouped by the provider's segment key. A segment's final text is its last final; its
  partials are the partial events that arrived before its first final, with consecutive duplicates dropped.
  The final transcript is every segment's final in the order the segments were first finalised.
- Partial stability, all on the primary metric's tokens (so casing and punctuation that providers only add
  to finals never count as rewrites):
  - `partial_rewrite_rate` = rewritten / partial tokens. Each partial is aligned (minimum edit) to its
    segment's final; a partial token is rewritten when it is substituted or deleted in that alignment, i.e. it
    did not survive into the final at its place. A segment with partials but no final rewrites all of them.
  - `revisions_per_word` = revisions / final tokens. For each final token, take the token each partial
    showed at that place (from the same alignment, absent if the partial had nothing there) followed by the
    final token itself; every step where a shown token is followed by a different or absent one is a revision.
- Final transcript error: the batch primary metric (same normalisation, tokenizer and alignment).
- Word timing: the batch caption timing error (`clipbench.scoring.timing.timing_error`) on the finals' words.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from clipbench.models import Word
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import primary_metric
from clipbench.scoring.timing import timing_error
from clipbench.streaming.base import StreamEvent, StreamTranscript

Tokenizer = Callable[[str], list[str]]


@dataclass
class Segment:
    key: str
    partials: list[str]
    final: str | None
    final_words: list[Word] | None
    first_final_ms: float | None


def segments(events: Sequence[StreamEvent]) -> list[Segment]:
    by_key: dict[str, Segment] = {}
    order: list[str] = []
    for e in sorted(events, key=lambda e: e.t_ms):
        seg = by_key.get(e.segment)
        if seg is None:
            seg = by_key[e.segment] = Segment(e.segment, [], None, None, None)
            order.append(e.segment)
        if e.kind == "partial":
            if seg.first_final_ms is None and (not seg.partials or seg.partials[-1] != e.text):
                seg.partials.append(e.text)
        else:
            if seg.first_final_ms is None:
                seg.first_final_ms = e.t_ms
            seg.final = e.text
            seg.final_words = e.words
    finalised = sorted((s for s in by_key.values() if s.first_final_ms is not None), key=lambda s: s.first_final_ms)
    open_ = [by_key[k] for k in order if by_key[k].first_final_ms is None]
    return finalised + open_


def final_text(events: Sequence[StreamEvent]) -> str:
    return " ".join(s.final for s in segments(events) if s.final)


def final_words(events: Sequence[StreamEvent]) -> list[Word] | None:
    words: list[Word] = []
    any_words = False
    for s in segments(events):
        if s.final_words:
            any_words = True
            words.extend(s.final_words)
    return words if any_words else None


@dataclass
class Stability:
    partial_tokens: int = 0
    rewritten_tokens: int = 0
    final_tokens: int = 0
    revisions: int = 0

    @property
    def rewrite_rate(self) -> float | None:
        return self.rewritten_tokens / self.partial_tokens if self.partial_tokens else None

    @property
    def revisions_per_word(self) -> float | None:
        return self.revisions / self.final_tokens if self.final_tokens else None


def segment_stability(partials: Sequence[list[str]], final: list[str]) -> Stability:
    out = Stability(final_tokens=len(final))
    shown: list[list[str | None]] = []  # per partial: the token shown at each final position
    for p in partials:
        out.partial_tokens += len(p)
        a = align(p, final)  # ref = partial, hyp = final
        out.rewritten_tokens += a.subs + a.dels
        b = align(final, p)  # ref = final, so every final position gets the partial token aligned to it
        at: list[str | None] = [None] * len(final)
        for op in b.ops:
            if op.op in ("equal", "sub"):
                at[op.ref_index] = op.hyp
        shown.append(at)
    for j, tok in enumerate(final):
        seq = [s[j] for s in shown] + [tok]
        out.revisions += sum(1 for k in range(len(seq) - 1) if seq[k] is not None and seq[k] != seq[k + 1])
    return out


def stability(segs: Sequence[Segment], tokenizer: Tokenizer) -> Stability:
    total = Stability()
    for s in segs:
        partials = [toks for toks in (tokenizer(p) for p in s.partials)]
        partials = [p for i, p in enumerate(partials) if p and (i == 0 or p != partials[i - 1])]
        part = segment_stability(partials, tokenizer(s.final) if s.final else [])
        total.partial_tokens += part.partial_tokens
        total.rewritten_tokens += part.rewritten_tokens
        total.final_tokens += part.final_tokens
        total.revisions += part.revisions
    return total


@dataclass
class StreamMetrics:
    metric: str
    ttfp_ms: float | None
    ttfs_ms: float | None
    n_partials: int
    n_finals: int
    n_segments: int
    partial_tokens: int
    rewritten_tokens: int
    partial_rewrite_rate: float | None
    final_tokens: int
    revisions: int
    revisions_per_word: float | None
    ref_len: int
    hits: int
    subs: int
    dels: int
    ins: int
    errors: int
    error_rate: float
    timing_n_words: int | None
    timing_median_ms: float | None
    timing_p90_ms: float | None
    timing_within_200ms: float | None
    hyp_chars: int

    def as_dict(self) -> dict:
        return asdict(self)


def clip_metrics(
    transcript: StreamTranscript, ref_text: str, ref_words: list[Word] | None, language: str
) -> StreamMetrics:
    spec = primary_metric(language)
    events = transcript.events
    with_text = [e for e in events if e.text.strip()]
    finals = [e for e in events if e.kind == "final"]
    ttfp = min((e.t_ms for e in with_text), default=None)
    ttfs = None
    if finals:
        ttfs = max(0.0, max(e.t_ms for e in finals) - transcript.audio_end_ms)
    segs = segments(events)
    stab = stability(segs, spec.tokenizer)
    hyp = final_text(events)
    a = align(spec.tokenizer(ref_text), spec.tokenizer(hyp))
    timing = timing_error(ref_words, final_words(events), spec.tokenizer)
    return StreamMetrics(
        metric=spec.name,
        ttfp_ms=ttfp,
        ttfs_ms=ttfs,
        n_partials=sum(1 for e in events if e.kind == "partial"),
        n_finals=len(finals),
        n_segments=sum(1 for s in segs if s.final),
        partial_tokens=stab.partial_tokens,
        rewritten_tokens=stab.rewritten_tokens,
        partial_rewrite_rate=stab.rewrite_rate,
        final_tokens=stab.final_tokens,
        revisions=stab.revisions,
        revisions_per_word=stab.revisions_per_word,
        ref_len=a.ref_len,
        hits=a.hits,
        subs=a.subs,
        dels=a.dels,
        ins=a.ins,
        errors=a.errors,
        error_rate=a.error_rate,
        timing_n_words=None if timing is None else timing.n_words,
        timing_median_ms=None if timing is None else timing.median_ms,
        timing_p90_ms=None if timing is None else timing.p90_ms,
        timing_within_200ms=None if timing is None else timing.within_200ms,
        hyp_chars=len(hyp),
    )
