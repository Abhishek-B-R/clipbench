"""Code-switch accuracy: error rate on the reference tokens inside tagged code-switch spans.

Spans are character offsets into the verified (corrected) reference text. See
METHODOLOGY.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clipbench.models import CodeSwitchSpan
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import MetricSpec, whitespace_tokens


def _merge(intervals: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        start, end = max(0, start), min(n, end)
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _snap_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a span so it does not cut a word in half."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def flagged_tokens(text: str, spans: Sequence[CodeSwitchSpan], spec: MetricSpec) -> tuple[list[str], list[bool]]:
    """Tokenise the reference piece by piece and flag the tokens that come from inside a span.

    For word metrics, spans are first widened to whole words.
    """
    snap = spec.split is whitespace_tokens
    spans_ = _merge([(s.start, s.end) for s in spans], len(text))
    if snap:
        spans_ = _merge([_snap_to_words(text, a, b) for a, b in spans_], len(text))
    tokens: list[str] = []
    flags: list[bool] = []
    pos = 0
    for start, end in spans_:
        for piece, flag in ((text[pos:start], False), (text[start:end], True)):
            toks = spec.tokenizer(piece)
            tokens += toks
            flags += [flag] * len(toks)
        pos = end
    toks = spec.tokenizer(text[pos:])
    tokens += toks
    flags += [False] * len(toks)
    return tokens, flags


@dataclass
class CodeSwitch:
    ref_len: int
    errors: int

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.ref_len if self.ref_len else None


def codeswitch_error(
    ref_text: str, spans: Sequence[CodeSwitchSpan] | None, hyp_tokens: list[str], spec: MetricSpec
) -> CodeSwitch | None:
    """Errors on flagged reference tokens: substitutions and deletions of flagged tokens, plus
    insertions whose neighbours on both sides are flagged (the text edges count as flagged).
    None when the clip has no spans."""
    if not spans:
        return None
    tokens, flags = flagged_tokens(ref_text, spans, spec)
    n = sum(flags)
    if n == 0:
        return CodeSwitch(0, 0)
    a = align(tokens, hyp_tokens)
    errors = 0
    for o in a.ops:
        if o.op in ("sub", "del"):
            errors += flags[o.ref_index]
        elif o.op == "ins":
            left = flags[o.ref_index - 1] if o.ref_index > 0 else True
            right = flags[o.ref_index] if o.ref_index < len(flags) else True
            errors += left and right
    return CodeSwitch(n, int(errors))
