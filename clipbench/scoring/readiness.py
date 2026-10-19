"""Caption readiness: punctuation F1 and casing accuracy on un-normalised text.

Scored only on clips whose verified text the reviewer marked `punctuated`, and separately from CER/WER,
so a provider is not punished twice for the same mistake. See docs/methodology_v2_metrics.md.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import CHAR_LANGUAGES, nfkc, strip_bracket_tags

PunctClass = Literal["final", "comma"]

# After NFKC, full width ！？，：； are ASCII. 。 and 、 are not changed by NFKC.
FINAL_MARKS = set(".?!。…।॥")
COMMA_MARKS = set(",;:、")


@dataclass
class Unit:
    key: str  # what is aligned (lowercased core)
    raw: str  # the core as written, for casing
    punct: PunctClass | None = None  # the mark class right after this unit


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("P", "S")


def _class_of(marks: str) -> PunctClass | None:
    if any(m in FINAL_MARKS for m in marks):
        return "final"
    if any(m in COMMA_MARKS for m in marks):
        return "comma"
    return None


def _attach(units: list[Unit], marks: str) -> None:
    cls = _class_of(marks)
    if units and cls and units[-1].punct != "final":  # final wins over comma
        units[-1].punct = cls


def units(text: str, language: str) -> list[Unit]:
    """Split text into units that carry the punctuation mark following them.

    Character languages (ja): every non-space, non-punctuation character is a unit.
    Others: every whitespace separated word with leading and trailing punctuation removed is a unit.
    Punctuation that stands alone (or leads the next word, like ¿) attaches to the previous unit.
    """
    text = strip_bracket_tags(nfkc(text or ""))
    out: list[Unit] = []
    if language.split("-")[0] in CHAR_LANGUAGES:
        for ch in text:
            if ch.isspace():
                continue
            if _is_punct(ch):
                _attach(out, ch)
            else:
                out.append(Unit(ch.lower(), ch))
        return out
    for piece in text.split():
        start, end = 0, len(piece)
        while start < end and _is_punct(piece[start]):
            start += 1
        while end > start and _is_punct(piece[end - 1]):
            end -= 1
        _attach(out, piece[:start])
        core = piece[start:end]
        if core:
            out.append(Unit(core.lower(), core))
        _attach(out, piece[end:])
    return out


def _has_latin(text: str) -> bool:
    return any(ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN") for ch in text)


@dataclass
class Readiness:
    punct_tp: int
    punct_fp: int
    punct_fn: int
    casing_n: int
    casing_correct: int

    @property
    def punct_f1(self) -> float | None:
        denom = 2 * self.punct_tp + self.punct_fp + self.punct_fn
        return 2 * self.punct_tp / denom if denom else None

    @property
    def casing_acc(self) -> float | None:
        return self.casing_correct / self.casing_n if self.casing_n else None


def readiness(ref_text: str, hyp_text: str, language: str) -> Readiness:
    """Punctuation and casing agreement at aligned positions.

    Units are aligned on their lowercased cores. At every aligned pair (hit or substitution) the
    mark classes after the two units are compared: same class is a true positive; a mark only in the
    hypothesis, or of the other class, is a false positive; a mark only in the reference, or of the
    other class, is a false negative. Deleted and inserted units are ignored (already counted by WER).
    Casing is checked on hits whose reference unit has Latin letters: correct when written identically.
    """
    ref_units, hyp_units = units(ref_text, language), units(hyp_text, language)
    a = align([u.key for u in ref_units], [u.key for u in hyp_units])
    tp = fp = fn = cn = cc = 0
    for o in a.ops:
        if o.op not in ("equal", "sub"):
            continue
        r, h = ref_units[o.ref_index], hyp_units[o.hyp_index]
        if r.punct and r.punct == h.punct:
            tp += 1
        else:
            fp += h.punct is not None
            fn += r.punct is not None
        if o.op == "equal" and _has_latin(r.raw):
            cn += 1
            cc += r.raw == h.raw
    return Readiness(tp, fp, fn, cn, cc)
