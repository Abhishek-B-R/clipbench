"""Formatting (A6): punctuation error rate, casing accuracy and inverse text normalisation accuracy.

- Punctuation error rate (Picovoice style): (substitutions + deletions + insertions of punctuation marks)
  over reference marks, compared at aligned word (or character, for ja) positions.
- Casing accuracy: `clipbench.scoring.readiness` (unchanged).
- ITN accuracy on the reference's `itn_spans`: is the span written in the same form as the reference
  (digits or words, date, time, currency, percent and unit formats), with a per kind breakdown.

Writes results/<run_id>/formatting.parquet and formatting_aggregates.parquet. Numbers only.
See METHODOLOGY.md.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import ITN_KINDS, ItnSpan
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import CHAR_LANGUAGES, nfkc, primary_metric
from clipbench.scoring.quality_io import QualityRef, grouped, load_run, rate_ci, results_path
from clipbench.scoring.readiness import readiness

MarkClass = Literal["period", "question", "exclamation", "comma"]

# After NFKC, full width ！？，：； are ASCII.
_MARKS: dict[str, MarkClass] = {
    ".": "period", "。": "period", "…": "period", "।": "period", "॥": "period",
    "?": "question", "!": "exclamation",
    ",": "comma", "、": "comma", ";": "comma", ":": "comma",
}  # fmt: skip
_PRIORITY: list[MarkClass] = ["question", "exclamation", "period", "comma"]
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|（[^（）]*）|［[^［］]*］")


def _is_punct(ch: str) -> bool:
    return unicodedata.category(nfkc(ch)[:1] or ch)[0] in ("P", "S")


def mark_class(marks: str) -> MarkClass | None:
    found = {_MARKS[c] for c in nfkc(marks) if c in _MARKS}
    for cls in _PRIORITY:
        if cls in found:
            return cls
    return None


@dataclass
class Unit:
    key: str  # aligned on: NFKC lowercased core
    raw: str  # core as written
    start: int  # code point offsets of the core in the original text
    end: int
    piece_start: int  # offsets of the whole whitespace piece, punctuation included
    piece_end: int
    mark: MarkClass | None = None  # punctuation right after this unit


def _blank_brackets(text: str) -> str:
    """Bracket annotations become spaces of the same length, so offsets stay valid."""
    previous = None
    while previous != text:
        previous = text
        text = _BRACKET_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _attach(units: list[Unit], marks: str) -> None:
    cls = mark_class(marks)
    if not units or cls is None:
        return
    cur = units[-1].mark
    if cur is None or _PRIORITY.index(cls) < _PRIORITY.index(cur):
        units[-1].mark = cls


def units(text: str, language: str) -> list[Unit]:
    """Offset preserving units (see readiness.units): characters for ja, words elsewhere."""
    text = _blank_brackets(text or "")
    out: list[Unit] = []
    if language.split("-")[0] in CHAR_LANGUAGES:
        for i, ch in enumerate(text):
            if ch.isspace():
                continue
            if _is_punct(ch):
                _attach(out, ch)
            else:
                out.append(Unit(nfkc(ch).lower(), ch, i, i + 1, i, i + 1))
        return out
    for m in re.finditer(r"\S+", text):
        piece, base = m.group(0), m.start()
        start, end = 0, len(piece)
        while start < end and _is_punct(piece[start]):
            start += 1
        while end > start and _is_punct(piece[end - 1]):
            end -= 1
        _attach(out, piece[:start])
        core = piece[start:end]
        if core:
            out.append(Unit(nfkc(core).lower(), core, base + start, base + end, m.start(), m.end()))
        _attach(out, piece[end:])
    return out


# ---------------------------------------------------------------------------
# Punctuation error rate
# ---------------------------------------------------------------------------


@dataclass
class Punctuation:
    ref_marks: int
    subs: int
    dels: int
    ins: int

    @property
    def errors(self) -> int:
        return self.subs + self.dels + self.ins

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.ref_marks if self.ref_marks else None


def punctuation_error(ref_text: str, hyp_text: str, language: str) -> Punctuation:
    """At every aligned unit pair (hit or substitution): a reference mark the hypothesis lacks is a
    deletion, a hypothesis mark the reference lacks is an insertion, two different classes are a
    substitution. Units only on one side are left to WER. Classes: period, question, exclamation, comma."""
    r_units, h_units = units(ref_text, language), units(hyp_text, language)
    a = align([u.key for u in r_units], [u.key for u in h_units])
    n = s = d = i = 0
    for o in a.ops:
        if o.op not in ("equal", "sub"):
            continue
        r, h = r_units[o.ref_index].mark, h_units[o.hyp_index].mark
        n += r is not None
        if r and h and r != h:
            s += 1
        elif r and not h:
            d += 1
        elif h and not r:
            i += 1
    return Punctuation(n, s, d, i)


# ---------------------------------------------------------------------------
# Aligned spans (shared with entities)
# ---------------------------------------------------------------------------


def aligned_hyp_span(
    ref_units: Sequence[Unit],
    hyp_units: Sequence[Unit],
    ops: Sequence[Any],
    start: int,
    end: int,
) -> tuple[int, int] | None:
    """Hypothesis piece offsets aligned to the reference characters [start, end).

    Reference units overlapping the span are found; the hypothesis units aligned to them (hits and
    substitutions) plus insertions right before, between or right after them give the hypothesis range,
    widened to whole pieces (so attached symbols like $ and % come along). None when every reference
    unit in the span was deleted."""
    idx = [k for k, u in enumerate(ref_units) if u.start < end and u.end > start]
    if not idx:
        return None
    lo, hi = idx[0], idx[-1]
    hyp_idx = [o.hyp_index for o in ops if o.op in ("equal", "sub") and lo <= o.ref_index <= hi]
    if not hyp_idx:
        return None
    hyp_idx += [o.hyp_index for o in ops if o.op == "ins" and lo <= o.ref_index <= hi + 1]
    first, last = hyp_units[min(hyp_idx)], hyp_units[max(hyp_idx)]
    return first.piece_start, last.piece_end


def span_alignment(ref_text: str, hyp_text: str, language: str):
    r_units, h_units = units(ref_text, language), units(hyp_text, language)
    a = align([u.key for u in r_units], [u.key for u in h_units])
    return r_units, h_units, a.ops


# ---------------------------------------------------------------------------
# ITN
# ---------------------------------------------------------------------------

_EDGE_PUNCT = ".,!?;:、。…\"'«»“”‘’()[]¿¡"


def written_form(text: str) -> str:
    """NFKC, casefolded, whitespace collapsed, sentence punctuation stripped from both ends."""
    text = re.sub(r"\s+", " ", nfkc(text or "")).strip().casefold()
    return text.strip(_EDGE_PUNCT + " ")


def shape(text: str) -> str:
    """Rendering signature: digit runs become 9, letter runs a, spaces dropped, symbols kept.
    '$1,000' -> '$9,9', 'March 3, 2024' -> 'a9,9', 'three' -> 'a'."""
    out = []
    for ch in written_form(text):
        if ch.isdigit():
            c = "9"
        elif ch.isalpha():
            c = "a"
        elif ch.isspace():
            continue
        else:
            c = ch
        if not out or out[-1] != c or c not in "9a":
            out.append(c)
    return "".join(out)


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def numbers(text: str) -> list[float]:
    """Numeric values written with digits: '1,000' and '1.000' are thousands, '5.00' and '5,5' decimals."""
    out = []
    for m in _NUM_RE.finditer(nfkc(text or "")):
        raw = m.group(0)
        groups = re.split(r"[.,]", raw)
        if len(groups) > 1 and all(len(g) == 3 for g in groups[1:]):
            out.append(float("".join(groups)))
        elif len(groups) == 2:
            out.append(float(f"{groups[0]}.{groups[1]}"))
        else:
            out.append(float("".join(groups)))
    return out


@dataclass
class ItnResult:
    kind: str
    found: bool  # some hypothesis text aligns to the span
    form_match: bool  # written exactly as the reference (after written_form)
    style_match: bool  # same rendering shape (digits vs words, separators, symbols)
    value_match: bool  # same content after the language's metric normaliser, or the same numeric values


def itn_accuracy(ref_text: str, hyp_text: str, spans: Sequence[ItnSpan], language: str, alignment=None) -> list[ItnResult]:
    if not spans:
        return []
    r_units, h_units, ops = alignment or span_alignment(ref_text, hyp_text, language)
    spec = primary_metric(language)
    out = []
    for sp in spans:
        ref_piece = ref_text[sp.start : sp.end]
        rng = aligned_hyp_span(r_units, h_units, ops, sp.start, sp.end)
        if rng is None:
            out.append(ItnResult(sp.kind, False, False, False, False))
            continue
        hyp_piece = hyp_text[rng[0] : rng[1]]
        form = written_form(ref_piece) == written_form(hyp_piece)
        style = shape(ref_piece) == shape(hyp_piece)
        rn, hn = numbers(ref_piece), numbers(hyp_piece)
        value = spec.tokenizer(ref_piece) == spec.tokenizer(hyp_piece) or (bool(rn) and rn == hn)
        out.append(ItnResult(sp.kind, True, form, style, value or form))
    return out


# ---------------------------------------------------------------------------
# Per run
# ---------------------------------------------------------------------------

FORMATTING_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "category", "conditions", "punctuated",
    "punct_ref_marks", "punct_subs", "punct_dels", "punct_ins", "punct_errors", "punct_error_rate",
    "casing_n", "casing_correct", "casing_acc",
    "itn_n", "itn_found", "itn_form_match", "itn_style_match", "itn_value_match", "itn_form_acc",
    *[f"itn_{k}_{m}" for k in ITN_KINDS for m in ("n", "form_match", "style_match", "value_match")],
]  # fmt: skip


def clip_formatting(ref: QualityRef, hyp_text: str, language: str) -> dict[str, Any]:
    rec: dict[str, Any] = {c: None for c in FORMATTING_COLUMNS}
    if ref.punctuated:
        p = punctuation_error(ref.text, hyp_text, language)
        rd = readiness(ref.text, hyp_text, language)
        rec.update(
            punct_ref_marks=p.ref_marks, punct_subs=p.subs, punct_dels=p.dels, punct_ins=p.ins,
            punct_errors=p.errors, punct_error_rate=p.error_rate,
            casing_n=rd.casing_n, casing_correct=rd.casing_correct, casing_acc=rd.casing_acc,
        )  # fmt: skip
    if ref.itn_spans:
        res = itn_accuracy(ref.text, hyp_text, ref.itn_spans, language)
        n = len(res)
        rec.update(
            itn_n=n,
            itn_found=sum(r.found for r in res),
            itn_form_match=sum(r.form_match for r in res),
            itn_style_match=sum(r.style_match for r in res),
            itn_value_match=sum(r.value_match for r in res),
            itn_form_acc=sum(r.form_match for r in res) / n,
        )
        for k in ITN_KINDS:
            mine = [r for r in res if r.kind == k]
            rec[f"itn_{k}_n"] = len(mine)
            rec[f"itn_{k}_form_match"] = sum(r.form_match for r in mine)
            rec[f"itn_{k}_style_match"] = sum(r.style_match for r in mine)
            rec[f"itn_{k}_value_match"] = sum(r.value_match for r in mine)
    return rec


def formatting_frame(run_id: str, settings: Settings) -> pd.DataFrame | None:
    data = load_run(run_id, settings)
    if data is None:
        return None
    records = []
    for row, ref, hyp in data.pairs():
        if not ref.punctuated and not ref.itn_spans:
            continue
        rec = clip_formatting(ref, hyp, row.language)
        rec.update(
            provider=row.provider, model_id=row.model_id, video_id=row.video_id, tier=row.tier,
            language=row.language, category=ref.category, conditions=json.dumps(ref.all_conditions),
            punctuated=ref.punctuated,
        )  # fmt: skip
        records.append(rec)
    return pd.DataFrame(records, columns=FORMATTING_COLUMNS)


def _sum(group: pd.DataFrame, col: str) -> float:
    return float(group[col].fillna(0).sum()) if col in group else 0.0


def _metrics(seed: int):
    def fn(group: pd.DataFrame) -> dict[str, Any]:
        pu = group[group["punct_ref_marks"].notna()]
        it = group[group["itn_n"].fillna(0) > 0]
        per = rate_ci(pu["punct_errors"], pu["punct_ref_marks"], seed)
        cas = rate_ci(pu["casing_correct"], pu["casing_n"], seed)
        form = rate_ci(it["itn_form_match"], it["itn_n"], seed)
        n_itn = _sum(it, "itn_n")
        return {
            "punct_n_clips": int(len(pu)),
            "punct_ref_marks": int(_sum(pu, "punct_ref_marks")),
            "punct_error_rate": per[0], "punct_ci_low": per[1], "punct_ci_high": per[2],
            "casing_n": int(_sum(pu, "casing_n")),
            "casing_acc": cas[0], "casing_ci_low": cas[1], "casing_ci_high": cas[2],
            "itn_n_clips": int(len(it)),
            "itn_n": int(n_itn),
            "itn_form_acc": form[0], "itn_ci_low": form[1], "itn_ci_high": form[2],
            "itn_style_acc": _sum(it, "itn_style_match") / n_itn if n_itn else np.nan,
            "itn_value_acc": _sum(it, "itn_value_match") / n_itn if n_itn else np.nan,
            "itn_found_rate": _sum(it, "itn_found") / n_itn if n_itn else np.nan,
        }  # fmt: skip

    return fn


def _kind_metrics(seed: int):
    """ITN accuracy for one kind, as slice_kind = itn_kind."""

    def make(kind: str):
        def fn(group: pd.DataFrame) -> dict[str, Any]:
            n_col = f"itn_{kind}_n"
            g = group[group[n_col].fillna(0) > 0]
            form = rate_ci(g[f"itn_{kind}_form_match"], g[n_col], seed)
            n = _sum(g, n_col)
            return {
                "itn_n_clips": int(len(g)),
                "itn_n": int(n),
                "itn_form_acc": form[0], "itn_ci_low": form[1], "itn_ci_high": form[2],
                "itn_style_acc": _sum(g, f"itn_{kind}_style_match") / n if n else np.nan,
                "itn_value_acc": _sum(g, f"itn_{kind}_value_match") / n if n else np.nan,
            }  # fmt: skip

        return fn

    return make


def formatting_aggregates(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    parts = [grouped(df, _metrics(seed))]
    make = _kind_metrics(seed)
    for kind in ITN_KINDS:
        sub = df[df[f"itn_{kind}_n"].fillna(0) > 0]
        if sub.empty:
            continue
        agg = grouped(sub.assign(conditions="[]", category=None), make(kind))
        agg = agg[agg["slice_kind"] == "all"].assign(slice_kind="itn_kind", slice=kind)
        parts.append(agg)
    return pd.concat([p for p in parts if not p.empty], ignore_index=True)


def score_formatting(run_id: str, settings: Settings | None = None) -> Path | None:
    s = settings or get_settings()
    df = formatting_frame(run_id, s)
    if df is None:
        return None
    out = results_path(s, run_id, "formatting.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    formatting_aggregates(df, s.sample_seed).to_parquet(out.with_name("formatting_aggregates.parquet"), index=False)
    return out
