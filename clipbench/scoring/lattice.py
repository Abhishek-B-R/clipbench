"""Lattice scoring (A7, OI-WER style): error rate against the closest valid path through a reference lattice.

A reference becomes a lattice: a chain of segments, each segment holding one or more valid token
sequences. Valid alternatives come from the reference's `alternatives` spans and from built-in per
language equivalences (documented in METHODOLOGY.md). The hypothesis is aligned to
every path at once with one dynamic program over the lattice (a DAG), never by enumerating paths.

The strict path (the reference as written, tokenised whole) is always one of the valid paths, so the
lattice error count is never above the strict one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from clipbench.models import AlternativeSpan
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import (
    MetricSpec,
    english_number_words,
    japanese_number_reading,
    katakana_to_hiragana,
    loosen_latin,
    normalize_ja,
    transliterate_devanagari,
    whitespace_tokens,
)

Path = tuple[str, ...]

# ---------------------------------------------------------------------------
# Built-in equivalences
# ---------------------------------------------------------------------------

# Groups of written forms that are the same word for captions. Each entry is a group of surface forms;
# they are run through the metric's own normaliser and tokenizer, so they work for word and character
# metrics alike. Only the reference is expanded: a reference containing any member gets the other
# members as parallel branches.
PHRASE_GROUPS: dict[str, list[list[str]]] = {
    "en": [
        ["ok", "okay", "o.k."],
        ["alright", "all right"],
        ["percent", "per cent"],
        ["gray", "grey"],
        ["color", "colour"],
        ["favorite", "favourite"],
        ["center", "centre"],
        ["theater", "theatre"],
        ["realize", "realise"],
        ["organize", "organise"],
        ["mom", "mum"],
        ["cannot", "can not"],
        ["email", "e-mail"],
        ["online", "on-line"],
    ],
    "es": [
        ["ok", "okay", "okey", "oki"],
        ["solo", "sólo"],
        ["este", "éste"],
        ["esta", "ésta"],
        ["ese", "ése"],
        ["esa", "ésa"],
        ["aquel", "aquél"],
        ["guion", "guión"],
        ["truhan", "truhán"],
        ["periodo", "período"],
        ["septiembre", "setiembre"],
        ["video", "vídeo"],
        ["wifi", "wi-fi"],
        ["email", "e-mail"],
    ],
    "fr": [
        ["ok", "okay", "oké", "okey"],
        ["week-end", "weekend"],
        ["t-shirt", "tee-shirt", "tshirt"],
        ["e-mail", "email", "mail"],
        ["wifi", "wi-fi"],
        ["clé", "clef"],
        ["événement", "évènement"],
        ["oignon", "ognon"],
        ["paie", "paye"],
        ["pourcent", "pour cent"],
    ],
    "ko": [
        # loanword spellings: standard (국립국어원) form first, common variant after
        ["케이크", "케익"],
        ["파이팅", "화이팅"],
        ["카페", "까페"],
        ["주스", "쥬스"],
        ["메시지", "메세지"],
        ["콘텐츠", "컨텐츠"],
        ["액세서리", "악세사리"],
        ["초콜릿", "초콜렛"],
        ["리더십", "리더쉽"],
        ["로봇", "로보트"],
        ["오케이", "오키", "ok", "okay"],
    ],
    "ja": [
        ["ok", "okay", "オッケー", "オーケー"],
    ],
    "hi": [
        ["ok", "okay"],
    ],
}


def _english_year_reading(year: int) -> str | None:
    """'twenty twenty four' for 2024, 'nineteen oh five' for 1905. None where it equals the cardinal."""
    if not 1100 <= year <= 2099:
        return None
    head, tail = divmod(year, 100)
    if tail == 0:
        reading = f"{english_number_words(head)} hundred"
    elif tail < 10:
        reading = f"{english_number_words(head)} oh {english_number_words(tail)}"
    else:
        reading = f"{english_number_words(head)} {english_number_words(tail)}"
    return None if reading == english_number_words(year) else reading


@lru_cache(maxsize=1)
def _english_year_groups() -> tuple[tuple[str, str], ...]:
    """(cardinal words, year reading) for 1100..2099: 'two thousand twenty four' vs 'twenty twenty four'."""
    out = []
    for year in range(1100, 2100):
        reading = _english_year_reading(year)
        if reading:
            out.append((english_number_words(year), reading))
    return tuple(out)


_KANJI_DIGITS = "〇一二三四五六七八九"


def kanji_numeral(n: int) -> str:
    """Kanji numeral for 0 <= n < 10^12: 2024 is 二千二十四, 15000 is 一万五千."""
    if n == 0:
        return "〇"

    def below_10000(m: int) -> str:
        out = ""
        for value, name in ((1000, "千"), (100, "百"), (10, "十")):
            count, m = divmod(m, value)
            if count:
                out += ("" if count == 1 else _KANJI_DIGITS[count]) + name
        if m:
            out += _KANJI_DIGITS[m]
        return out

    out = ""
    for value, name in ((10**8, "億"), (10**4, "万")):
        count, n = divmod(n, value)
        if count:
            out += below_10000(count) + name
    return out + (below_10000(n) if n else "")


def _hiragana_to_katakana(text: str) -> str:
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in text)


# ---------------------------------------------------------------------------
# Lattice
# ---------------------------------------------------------------------------


@dataclass
class Lattice:
    """A chain of segments; each segment is a list of valid token paths (an empty path skips it)."""

    segments: list[list[Path]] = field(default_factory=list)
    strict: list[str] = field(default_factory=list)  # the reference as written, tokenised whole

    @property
    def n_paths_log10(self) -> float:
        return float(sum(np.log10(len(s)) for s in self.segments if s))

    @property
    def has_alternatives(self) -> bool:
        return any(len(s) > 1 for s in self.segments)


def _dedup(paths: Sequence[Path]) -> list[Path]:
    seen: dict[Path, None] = {}
    for p in paths:
        seen.setdefault(tuple(p), None)
    return list(seen)


def _merge_spans(spans: Sequence[AlternativeSpan], n: int) -> list[tuple[int, int, list[str]]]:
    """Clamp, sort and drop spans that overlap an earlier one (overlaps are a tagging error)."""
    out: list[tuple[int, int, list[str]]] = []
    for s in sorted(spans, key=lambda s: (s.start, s.end)):
        start, end = max(0, s.start), min(n, s.end)
        if end <= start or (out and start < out[-1][1]):
            continue
        out.append((start, end, list(s.options)))
    return out


def _snap(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def _phrase_paths(language: str, spec: MetricSpec) -> list[list[Path]]:
    groups = PHRASE_GROUPS.get(language, [])
    out: list[list[Path]] = []
    for group in groups:
        paths = _dedup([tuple(spec.tokenizer(g)) for g in group])
        paths = [p for p in paths if p]
        if len(paths) > 1:
            out.append(paths)
    if language == "en" and spec.split is whitespace_tokens:
        for cardinal, reading in _english_year_groups():
            out.append([tuple(cardinal.split()), tuple(reading.split())])
    return out


@lru_cache(maxsize=32)
def _phrase_index(language: str, spec: MetricSpec) -> dict[str, list[tuple[Path, list[Path]]]]:
    """First token -> [(member path, all paths of its group)], longest member first."""
    index: dict[str, list[tuple[Path, list[Path]]]] = {}
    for paths in _phrase_paths(language, spec):
        for p in paths:
            index.setdefault(p[0], []).append((p, paths))
    for v in index.values():
        v.sort(key=lambda x: -len(x[0]))
    return index


def _expand_phrases(tokens: list[str], language: str, spec: MetricSpec) -> list[list[Path]]:
    """Segments for a run of plain reference tokens, with built-in phrase groups as branches."""
    index = _phrase_index(language, spec)
    segments: list[list[Path]] = []
    i = 0
    while i < len(tokens):
        hit = None
        for member, paths in index.get(tokens[i], []):
            if tuple(tokens[i : i + len(member)]) == member:
                hit = (member, paths)
                break
        if hit is None:
            segments.append([(tokens[i],)])
            i += 1
            continue
        member, paths = hit
        segments.append(_dedup([member, *paths]))
        i += len(member)
    return segments


def _ja_segments(piece: str, spec: MetricSpec) -> list[list[Path]]:
    """Japanese built-ins: every morpheme may be written as its surface, in hiragana or in katakana, and
    digit runs also as their reading or kanji numeral. Only for character metrics on the base normaliser."""
    from clipbench.scoring.normalize import _tagger

    norm = normalize_ja(piece)
    if not norm:
        return []
    segments: list[list[Path]] = []
    for w in _tagger()(norm):
        surface = w.surface
        options = [surface]
        if surface.isdigit() and surface.isascii():
            n = int(surface)
            options += [japanese_number_reading(n), kanji_numeral(n) if n < 10**12 else surface]
        else:
            kana = getattr(w.feature, "kana", None)
            if kana and kana != "*":
                hira = katakana_to_hiragana(kana)
                options += [hira, _hiragana_to_katakana(hira)]
        segments.append(_dedup([tuple(spec.split(spec.normalize(o))) for o in options]))
    return [s for s in segments if any(s)]


def _plain_segments(piece: str, language: str, spec: MetricSpec) -> list[list[Path]]:
    if language == "ja" and spec.name == "cer":
        return _ja_segments(piece, spec)
    return _expand_phrases(spec.tokenizer(piece), language, spec)


def build_lattice(
    ref_text: str, alternatives: Sequence[AlternativeSpan] | None, spec: MetricSpec, language: str
) -> Lattice:
    """Reference lattice for one metric.

    The text is cut at the alternative spans (widened to whole words for word metrics). Each span becomes
    one segment whose paths are the reference text and every option, tokenised with the metric's own
    tokenizer; the text between spans gets the built-in equivalences of the language.
    """
    language = language.split("-")[0].lower()
    text = ref_text or ""
    snap = spec.split is whitespace_tokens
    spans = _merge_spans(alternatives or [], len(text))
    if snap:
        snapped = []
        for start, end, options in spans:
            s2, e2 = _snap(text, start, end)
            # keep the parts of the widened word around the span on every option
            prefix, suffix = text[s2:start], text[end:e2]
            if snapped and s2 < snapped[-1][1]:
                continue
            snapped.append((s2, e2, [prefix + o + suffix for o in options]))
        spans = snapped
    segments: list[list[Path]] = []
    pos = 0
    for start, end, options in spans:
        segments += _plain_segments(text[pos:start], language, spec)
        paths = _dedup([tuple(spec.tokenizer(text[start:end]))] + [tuple(spec.tokenizer(o)) for o in options])
        segments.append(paths)
        pos = end
    segments += _plain_segments(text[pos:], language, spec)
    return Lattice(segments=segments, strict=spec.tokenizer(text))


# ---------------------------------------------------------------------------
# Token keys (equivalence when comparing one token to one token)
# ---------------------------------------------------------------------------


def _hi_key(token: str) -> str:
    """Hindi: Devanagari and casual romanisation compare equal (है and hai)."""
    return loosen_latin(transliterate_devanagari(token))


def token_key(language: str, spec: MetricSpec) -> Callable[[str], str]:
    language = language.split("-")[0].lower()
    if language == "hi" and spec.split is whitespace_tokens:
        return _hi_key
    return lambda t: t


# ---------------------------------------------------------------------------
# Dynamic program over the lattice
# ---------------------------------------------------------------------------


@dataclass
class LatticeAlignment:
    hits: int = 0
    subs: int = 0
    dels: int = 0
    ins: int = 0
    path: list[str] = field(default_factory=list)  # the closest valid reference path

    @property
    def errors(self) -> int:
        return self.subs + self.dels + self.ins

    @property
    def ref_len(self) -> int:
        return self.hits + self.subs + self.dels

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.ref_len, 1)


_START = -1


def _nodes(lattice: Lattice) -> tuple[list[str], list[list[int]], list[int]]:
    """Flatten segments into nodes (tokens) with predecessor lists. Returns (tokens, preds, finals)."""
    tokens: list[str] = []
    preds: list[list[int]] = []
    frontier = [_START]
    for seg in lattice.segments:
        new_frontier: list[int] = []
        for path in seg:
            if not path:
                new_frontier += frontier
                continue
            prev = list(frontier)
            for tok in path:
                tokens.append(tok)
                preds.append(prev)
                prev = [len(tokens) - 1]
            new_frontier += prev
        frontier = sorted(set(new_frontier))
    return tokens, preds, frontier


def align_lattice(lattice: Lattice, hyp_tokens: Sequence[str], key: Callable[[str], str] | None = None) -> LatticeAlignment:
    """Minimum edit alignment of the hypothesis to the closest path of the lattice.

    D[v][j] is the cheapest alignment of hyp[:j] to a lattice prefix ending with node v. For each node in
    topological order: substitution or hit from a predecessor at j - 1, deletion of v from a predecessor
    at j, insertion of hyp[j - 1] after v from D[v][j - 1]. The answer is the cheapest final node at m.
    Cost is O(nodes x hypothesis length x predecessors).
    """
    key = key or (lambda t: t)
    hyp = list(hyp_tokens)
    m = len(hyp)
    hyp_keys = [key(h) for h in hyp]
    tokens, preds, finals = _nodes(lattice)
    j_idx = np.arange(m + 1)
    start_row = j_idx.astype(np.int64)
    rows: list[np.ndarray] = []
    # backpointers: kind 0 diag, 1 del, 2 ins; pred index per (node, j)
    kinds: list[np.ndarray] = []
    from_pred: list[np.ndarray] = []
    big = np.iinfo(np.int64).max // 4

    def row_of(p: int) -> np.ndarray:
        return start_row if p == _START else rows[p]

    for v, tok in enumerate(tokens):
        k = key(tok)
        sub_cost = np.array([0 if hk == k else 1 for hk in hyp_keys], dtype=np.int64)
        best = np.full(m + 1, big, dtype=np.int64)
        kind = np.full(m + 1, 1, dtype=np.int8)
        pred_of = np.full(m + 1, _START, dtype=np.int64)
        for p in preds[v]:
            pr = row_of(p)
            dele = pr + 1
            better = dele < best
            best = np.where(better, dele, best)
            kind = np.where(better, 1, kind)
            pred_of = np.where(better, p, pred_of)
            if m:
                diag = np.concatenate(([big], pr[:-1] + sub_cost))
                better = diag < best  # deletion wins ties, then the first predecessor
                better[0] = False
                best = np.where(better, diag, best)
                kind = np.where(better, 0, kind)
                pred_of = np.where(better, p, pred_of)
        # insertions after v: D[j] = min(base[j], D[j-1] + 1) = j + cummin(base[k] - k)
        if m:
            with_ins = j_idx + np.minimum.accumulate(best - j_idx)
            ins = with_ins < best
            best = np.where(ins, with_ins, best)
            kind = np.where(ins, 2, kind)
        rows.append(best)
        kinds.append(kind)
        from_pred.append(pred_of)

    # pick the final node
    if not finals or finals == [_START]:
        return LatticeAlignment(ins=m)
    end = min(finals, key=lambda f: (int(row_of(f)[m]), f))
    if end == _START:
        return LatticeAlignment(ins=m)
    out = LatticeAlignment()
    v, j = end, m
    path: list[str] = []
    while v != _START:
        kd = int(kinds[v][j])
        if kd == 2:
            out.ins += 1
            j -= 1
            continue
        path.append(tokens[v])
        p = int(from_pred[v][j])
        if kd == 0:
            if key(tokens[v]) == hyp_keys[j - 1]:
                out.hits += 1
            else:
                out.subs += 1
            j -= 1
        else:
            out.dels += 1
        v = p
    out.ins += j  # hypothesis tokens before the first reference token
    out.path = path[::-1]
    return out


def lattice_score(
    ref_text: str,
    alternatives: Sequence[AlternativeSpan] | None,
    hyp_tokens: Sequence[str],
    spec: MetricSpec,
    language: str,
    lattice: Lattice | None = None,
) -> LatticeAlignment:
    """Closest valid path score. Never worse than the strict alignment of the whole reference."""
    lat = lattice or build_lattice(ref_text, alternatives, spec, language)
    best = align_lattice(lat, hyp_tokens, token_key(language, spec))
    strict = align(lat.strict, list(hyp_tokens))
    if strict.errors < best.errors or (strict.errors == best.errors and not lat.has_alternatives):
        return LatticeAlignment(strict.hits, strict.subs, strict.dels, strict.ins, list(lat.strict))
    return best
