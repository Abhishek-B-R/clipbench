"""Language-aware text normalisation and tokenisation for scoring.

Every rule is a small function so it can be tested and documented on its own.
`metrics_for(language)` returns the metric specs (name, primary flag, tokenizer) for a language.
docs/methodology_scoring.md writes out every rule in plain language.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------

# Square brackets hold sound tags like [音楽], [Música], [Applause].
# Round brackets hold annotations like (笑), (risas), speaker labels or furigana.
# Both are removed with their content. NFKC runs first, so full width brackets are ASCII by then.
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")
_WS_RE = re.compile(r"\s+")


def nfkc(text: str) -> str:
    """Unicode NFKC: full width ASCII to half width, half width kana to full width, etc."""
    return unicodedata.normalize("NFKC", text)


def lowercase(text: str) -> str:
    return text.lower()


def strip_bracket_tags(text: str) -> str:
    """Remove [..] and (..) annotations together with their content."""
    previous = None
    while previous != text:  # repeat so nested brackets are removed too
        previous = text
        text = _BRACKET_RE.sub(" ", text)
    return text


def _is_punct_or_symbol(ch: str) -> bool:
    # Unicode categories P* (punctuation) and S* (symbols, e.g. ♪ ~ ※ $).
    # The Japanese long vowel mark ー is category Lm and the iteration mark 々 is Lm, so both stay.
    return unicodedata.category(ch)[0] in ("P", "S")


def strip_punctuation(text: str, replacement: str = " ") -> str:
    """Replace every punctuation or symbol character with `replacement`."""
    return "".join(replacement if _is_punct_or_symbol(ch) else ch for ch in text)


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def remove_whitespace(text: str) -> str:
    return _WS_RE.sub("", text)


def strip_accents(text: str) -> str:
    """Drop all combining marks: á to a, ü to u, and also ñ to n."""
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", kept)


def whitespace_tokens(text: str) -> list[str]:
    return text.split()


def char_tokens(text: str) -> list[str]:
    return list(text)


# ---------------------------------------------------------------------------
# Spanish
# ---------------------------------------------------------------------------

_ES_UNITS = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho",
    "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco",
    "veintiséis", "veintisiete", "veintiocho", "veintinueve",
]  # fmt: skip
_ES_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_ES_HUNDREDS = {
    1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos", 5: "quinientos",
    6: "seiscientos", 7: "setecientos", 8: "ochocientos", 9: "novecientos",
}  # fmt: skip


def _es_below_1000(n: int) -> str:
    if n < 30:
        return _ES_UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _ES_TENS[tens] + (f" y {_ES_UNITS[unit]}" if unit else "")
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    return _ES_HUNDREDS[hundreds] + (f" {_es_below_1000(rest)}" if rest else "")


def _es_apocope(words: str) -> str:
    """'uno' becomes 'un' (and 'veintiuno' becomes 'veintiún') before mil / millón."""
    if words.endswith("veintiuno"):
        return words[: -len("veintiuno")] + "veintiún"
    if words.endswith("uno"):
        return words[:-3] + "un"
    return words


def spanish_number_words(n: int) -> str:
    """Spell a non-negative integer in Spanish. 0..999,999,999,999; larger numbers digit by digit."""
    if n < 0:
        return "menos " + spanish_number_words(-n)
    if n >= 10**12:
        return " ".join(_ES_UNITS[int(d)] for d in str(n))
    if n < 1000:
        return _es_below_1000(n)
    if n < 10**6:
        thousands, rest = divmod(n, 1000)
        head = "mil" if thousands == 1 else f"{_es_apocope(_es_below_1000(thousands))} mil"
        return head + (f" {_es_below_1000(rest)}" if rest else "")
    millions, rest = divmod(n, 10**6)
    head = "un millón" if millions == 1 else f"{_es_apocope(spanish_number_words(millions))} millones"
    return head + (f" {spanish_number_words(rest)}" if rest else "")


# 1.000 / 1,000 / 1 000 style thousands groups, then decimals, then plain integers.
_ES_GROUPED_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})+(?![\d]|[.,]\d)")
_ES_DECIMAL_RE = re.compile(r"(\d+)([.,])(\d+)")
_ES_INT_RE = re.compile(r"\d+")


def spanish_numbers_to_words(text: str) -> str:
    """Replace digits with Spanish words. '%' becomes 'por ciento'.

    '1.500' and '1,500' are read as thousands (one or more groups of exactly three digits).
    Other decimals read the separator: '3,5' is 'tres coma cinco', '3.5' is 'tres punto cinco'.
    """
    text = re.sub(r"(\d)\s*%", r"\1 por ciento", text)
    text = _ES_GROUPED_RE.sub(lambda m: spanish_number_words(int(re.sub(r"[.,]", "", m.group(0)))), text)

    def decimal(m: re.Match[str]) -> str:
        sep = "coma" if m.group(2) == "," else "punto"
        return f"{spanish_number_words(int(m.group(1)))} {sep} {spanish_number_words(int(m.group(3)))}"

    text = _ES_DECIMAL_RE.sub(decimal, text)
    return _ES_INT_RE.sub(lambda m: f" {spanish_number_words(int(m.group(0)))} ", text)


def normalize_es(text: str) -> str:
    """Spanish base normalisation used by `wer`."""
    text = nfkc(text)
    text = lowercase(text)
    text = strip_bracket_tags(text)
    text = spanish_numbers_to_words(text)
    text = strip_punctuation(text)  # includes ¿ and ¡
    return collapse_whitespace(text)


def normalize_es_no_accents(text: str) -> str:
    return strip_accents(normalize_es(text))


# ---------------------------------------------------------------------------
# Japanese
# ---------------------------------------------------------------------------

_JA_DIGITS = ["ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう"]
_JA_HUNDREDS = {1: "ひゃく", 3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}
_JA_THOUSANDS = {1: "せん", 3: "さんぜん", 8: "はっせん"}
_JA_BIG = [(10**12, "ちょう"), (10**8, "おく"), (10**4, "まん")]


def _ja_below_10000(n: int) -> str:
    out = []
    thousands, n = divmod(n, 1000)
    if thousands:
        out.append(_JA_THOUSANDS.get(thousands, _JA_DIGITS[thousands] + "せん"))
    hundreds, n = divmod(n, 100)
    if hundreds:
        out.append(_JA_HUNDREDS.get(hundreds, _JA_DIGITS[hundreds] + "ひゃく"))
    tens, units = divmod(n, 10)
    if tens:
        out.append("じゅう" if tens == 1 else _JA_DIGITS[tens] + "じゅう")
    if units:
        out.append(_JA_DIGITS[units])
    return "".join(out)


def japanese_number_reading(n: int) -> str:
    """Hiragana reading of a non-negative integer: 2024 is にせんにじゅうよん.

    Covers the common sound changes in hundreds and thousands (さんびゃく, ろっぴゃく, はっせん).
    Counter specific changes (いっぽん, はっぴき) are not modelled. 10^16 and above is read digit by digit.
    """
    if n == 0:
        return _JA_DIGITS[0]
    if n >= 10**16:
        return "".join(_JA_DIGITS[int(d)] for d in str(n))
    out = []
    for value, name in _JA_BIG:
        count, n = divmod(n, value)
        if count:
            out.append(_ja_below_10000(count) + name)
    if n:
        out.append(_ja_below_10000(n))
    return "".join(out)


def japanese_numbers_to_reading(text: str) -> str:
    """Replace ASCII digit runs (after NFKC) with their hiragana reading. Thousands commas are dropped."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return re.sub(r"\d+", lambda m: japanese_number_reading(int(m.group(0))), text)


def katakana_to_hiragana(text: str) -> str:
    """Map katakana ァ..ヶ to hiragana. ー and other characters are unchanged."""
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


@lru_cache(maxsize=1)
def _tagger():
    import fugashi

    return fugashi.Tagger()


@lru_cache(maxsize=1)
def _kakasi():
    import pykakasi

    return pykakasi.kakasi()


def _pykakasi_reading(surface: str) -> str:
    try:
        return "".join(item["hira"] for item in _kakasi().convert(surface))
    except Exception:  # pragma: no cover - pykakasi failure falls back to the surface
        return surface


def _token_reading(word) -> str:
    """Reading of one fugashi token: unidic kana field, else pykakasi, else the surface."""
    surface = word.surface
    kana = getattr(word.feature, "kana", None)
    if kana and kana != "*":
        return kana
    reading = _pykakasi_reading(surface)
    return reading or surface


def japanese_reading(text: str) -> str:
    """Hiragana reading of a text, so 漢字 vs かな spelling choices are not counted as errors."""
    text = japanese_numbers_to_reading(text)
    reading = "".join(_token_reading(w) for w in _tagger()(text))
    return katakana_to_hiragana(reading)


def normalize_ja(text: str) -> str:
    """Japanese base normalisation used by `cer`. Digits stay digits."""
    text = nfkc(text)
    text = lowercase(text)
    text = strip_bracket_tags(text)
    text = strip_punctuation(text, replacement="")
    return remove_whitespace(text)


def normalize_ja_reading(text: str) -> str:
    """Japanese reading normalisation used by `cer_reading`."""
    text = nfkc(text)
    text = lowercase(text)
    text = strip_bracket_tags(text)
    text = strip_punctuation(text, replacement=" ")  # spaces help the tokenizer, removed below
    text = japanese_reading(collapse_whitespace(text))
    text = strip_punctuation(text, replacement="")
    return remove_whitespace(text)


# ---------------------------------------------------------------------------
# Korean
# ---------------------------------------------------------------------------


def normalize_ko(text: str) -> str:
    """Korean base normalisation. Spacing is kept here; `cer_nospace` removes it."""
    text = nfkc(text)
    text = lowercase(text)
    text = strip_bracket_tags(text)
    text = strip_punctuation(text)
    return collapse_whitespace(text)


def normalize_ko_nospace(text: str) -> str:
    return remove_whitespace(normalize_ko(text))


# ---------------------------------------------------------------------------
# Metric specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    name: str
    is_primary: bool
    normalize: Callable[[str], str]
    split: Callable[[str], list[str]]

    def tokenizer(self, text: str) -> list[str]:
        return self.split(self.normalize(text or ""))


_METRICS: dict[str, list[MetricSpec]] = {
    "ja": [
        MetricSpec("cer", True, normalize_ja, char_tokens),
        MetricSpec("cer_reading", False, normalize_ja_reading, char_tokens),
    ],
    "es": [
        MetricSpec("wer", True, normalize_es, whitespace_tokens),
        MetricSpec("wer_no_accents", False, normalize_es_no_accents, whitespace_tokens),
    ],
    "ko": [
        MetricSpec("cer_nospace", True, normalize_ko_nospace, char_tokens),
        MetricSpec("wer_syllable", False, normalize_ko, whitespace_tokens),
    ],
}


def metrics_for(language: str) -> list[MetricSpec]:
    """Metric specs for a language, primary first."""
    base = language.split("-")[0].lower()
    if base not in _METRICS:
        raise ValueError(f"no scoring metrics defined for language {language!r}")
    return list(_METRICS[base])


def primary_metric(language: str) -> MetricSpec:
    return metrics_for(language)[0]
