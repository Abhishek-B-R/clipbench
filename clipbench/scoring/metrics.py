"""Alignment counts and hallucination measures from token lists.

Alignment uses jiwer (Levenshtein via rapidfuzz). Tokens are mapped to unique ids first so any
token, including a single Japanese character or a token containing spaces, is one jiwer word.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

import jiwer

from clipbench.models import Word

OpKind = Literal["equal", "sub", "del", "ins"]

GAP_MS = 1000  # a reference silence longer than this counts as "no speech"
LONG_OUTPUT_RATIO = 1.5  # hyp_len / ref_len above this is flagged as long output


class Op(NamedTuple):
    op: OpKind
    ref: str | None
    hyp: str | None
    ref_index: int  # index of the ref token; for an insertion, the ref position it is inserted before


@dataclass
class Alignment:
    hits: int = 0
    subs: int = 0
    dels: int = 0
    ins: int = 0
    ops: list[Op] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return self.subs + self.dels + self.ins

    @property
    def ref_len(self) -> int:
        return self.hits + self.subs + self.dels

    @property
    def hyp_len(self) -> int:
        return self.hits + self.subs + self.ins

    @property
    def error_rate(self) -> float:
        """errors / ref_len. With an empty reference this is the insertion count (errors / 1)."""
        return self.errors / max(self.ref_len, 1)

    def substitutions(self) -> list[tuple[str, str]]:
        return [(o.ref, o.hyp) for o in self.ops if o.op == "sub"]  # type: ignore[misc]


def align(ref_tokens: Sequence[str], hyp_tokens: Sequence[str]) -> Alignment:
    """Minimum edit alignment of two token lists."""
    ref, hyp = list(ref_tokens), list(hyp_tokens)
    if not ref or not hyp:
        ops = [Op("del", t, None, i) for i, t in enumerate(ref)] + [Op("ins", None, t, 0) for t in hyp]
        return Alignment(dels=len(ref), ins=len(hyp), ops=ops)

    ids: dict[str, str] = {}
    for tok in ref + hyp:
        ids.setdefault(tok, f"w{len(ids)}")
    out = jiwer.process_words(" ".join(ids[t] for t in ref), " ".join(ids[t] for t in hyp))

    a = Alignment()
    for chunk in out.alignments[0]:
        rs, re_, hs, he = chunk.ref_start_idx, chunk.ref_end_idx, chunk.hyp_start_idx, chunk.hyp_end_idx
        if chunk.type == "equal":
            for k in range(re_ - rs):
                a.ops.append(Op("equal", ref[rs + k], hyp[hs + k], rs + k))
            a.hits += re_ - rs
        elif chunk.type == "substitute":
            for k in range(re_ - rs):
                a.ops.append(Op("sub", ref[rs + k], hyp[hs + k], rs + k))
            a.subs += re_ - rs
        elif chunk.type == "delete":
            for k in range(re_ - rs):
                a.ops.append(Op("del", ref[rs + k], None, rs + k))
            a.dels += re_ - rs
        elif chunk.type == "insert":
            for k in range(he - hs):
                a.ops.append(Op("ins", None, hyp[hs + k], rs))
            a.ins += he - hs
    return a


# ---------------------------------------------------------------------------
# Hallucination
# ---------------------------------------------------------------------------


@dataclass
class Hallucination:
    hallucinated_tokens: float
    hallucination_rate: float
    length_ratio: float
    long_output: bool
    method: Literal["timing", "length"]


def _in_silence(mid_ms: float, ref_words: list[Word], gap_ms: int) -> bool:
    words = sorted(ref_words, key=lambda w: w.start_ms)
    if mid_ms < words[0].start_ms - gap_ms:
        return True
    last_end = max(w.end_ms for w in words)
    if mid_ms > last_end + gap_ms:
        return True
    covered_until = words[0].end_ms
    for w in words[1:]:
        if w.start_ms - covered_until > gap_ms and covered_until < mid_ms < w.start_ms:
            return True
        covered_until = max(covered_until, w.end_ms)
    return False


def hallucination(
    ref_len: int,
    hyp_len: int,
    ref_words: list[Word] | None = None,
    hyp_words: list[Word] | None = None,
    tokenizer: Callable[[str], list[str]] | None = None,
    *,
    gap_ms: int = GAP_MS,
    ratio_threshold: float = LONG_OUTPUT_RATIO,
) -> Hallucination:
    """Hallucination measures for one clip.

    Timing method (both sides have word timings): count hypothesis words whose midpoint falls in a
    reference silence longer than `gap_ms`, or more than `gap_ms` before the first / after the last
    reference word. Each such word counts as the number of metric tokens it contains (`tokenizer`).

    Length method (otherwise): hallucinated_tokens = max(0, hyp_len - ratio_threshold * ref_len),
    the token excess beyond the long output threshold.

    hallucination_rate = hallucinated_tokens / max(ref_len, 1) in both cases.
    length_ratio = hyp_len / max(ref_len, 1); long_output = length_ratio > ratio_threshold.
    """
    length_ratio = hyp_len / max(ref_len, 1)
    long_output = length_ratio > ratio_threshold
    if ref_words and hyp_words:
        count_tokens = tokenizer or (lambda s: s.split())
        tokens = 0.0
        for w in hyp_words:
            if _in_silence((w.start_ms + w.end_ms) / 2, ref_words, gap_ms):
                tokens += len(count_tokens(w.text))
        method: Literal["timing", "length"] = "timing"
    else:
        tokens = max(0.0, hyp_len - ratio_threshold * ref_len)
        method = "length"
    return Hallucination(tokens, tokens / max(ref_len, 1), length_ratio, long_output, method)
