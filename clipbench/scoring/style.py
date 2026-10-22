"""Filler, stutter and profanity handling (B18), reported as style properties, not errors.

Caption apps choose differently: some want "um" and stutters kept (verbatim), most want them dropped
(clean read); some want profanity written, others masked. This module measures what each provider does:

- fillers: how many reference fillers the hypothesis keeps, and fillers per minute in the hypothesis;
- repetitions and stutters ("I I", "b-but"): how many reference repetitions are kept;
- profanity: for each profane word in the reference, whether the hypothesis writes it, masks it
  ("f***", "[ __ ]", "[bleep]"), drops it or replaces it with another word; and bleeps marked in the
  reference.

Word lists per language are documented in METHODOLOGY.md. Writes
results/<run_id>/style.parquet and style_aggregates.parquet. Numbers only.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import nfkc, strip_bracket_tags
from clipbench.scoring.quality_io import grouped, load_run, rate_ci, results_path

MASK = "MASKEDTOKEN"

# Fillers that are always fillers.
FILLERS: dict[str, set[str]] = {
    "en": {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "hm", "mm", "mmm"},
    "es": {"eh", "ehh", "em", "emm", "ehm", "mm", "mmm", "hmm"},
    "ko": {"음", "으음", "어", "어어", "에", "흠", "엄"},
    "hi": {"um", "umm", "uh", "hmm", "अं", "उम्म", "हम्म", "अ"},
    "fr": {"euh", "euhh", "euuh", "heu", "hum", "hmm", "mm", "mmm", "bah", "ben", "beh"},
    "ja": {"えーっと", "えーと", "ええと", "えっと", "えと", "えー", "えぇ", "あのー", "あのう", "そのー", "うーん", "んー", "まー"},
}
# Words that are fillers only when followed by a pause (a comma, ellipsis, another filler or the end),
# because they are also ordinary words: Spanish este (this), Korean 그 (that), Hindi matlab (meaning),
# French genre (kind) and quoi (what).
PAUSE_FILLERS: dict[str, set[str]] = {
    "en": {"like"},
    "es": {"este", "pues", "bueno"},
    "ko": {"그", "저", "뭐", "막", "아", "저기"},
    "hi": {"matlab", "मतलब", "woh", "wo", "वो", "yaani", "यानी", "basically"},
    "fr": {"genre", "bon", "enfin", "quoi", "voilà", "bref"},
    "ja": {"あの", "その", "まあ", "なんか"},
}
# Word sequences that are pause fillers together (French "du coup", so then): every word of the phrase is a
# filler when the phrase is followed by a pause or a filler.
PAUSE_FILLER_PHRASES: dict[str, list[tuple[str, ...]]] = {
    "fr": [("du", "coup")],
}
# Profanity stems (prefix match on the lowercased word) per language.
PROFANITY: dict[str, list[str]] = {
    "en": ["fuck", "motherfuck", "shit", "bullshit", "bitch", "bastard", "asshole", "dick", "cunt", "damn",
           "goddamn", "piss", "crap", "wtf"],
    "es": ["mierda", "puta", "puto", "joder", "jodid", "coño", "carajo", "pendej", "cabrón", "cabron",
           "chingar", "chingad", "verga", "gilipollas", "hostia", "culero"],
    "ko": ["씨발", "시발", "씨바", "존나", "졸라", "개새끼", "새끼", "병신", "지랄", "젠장", "ㅅㅂ", "ㅈㄴ"],
    "hi": ["chutiya", "chutiye", "bhenchod", "behenchod", "madarchod", "gaandu", "gandu", "harami", "kamina",
           "kamine", "चूतिया", "चुतिया", "भेनचोद", "बहनचोद", "मादरचोद", "गांडू", "हरामी", "कमीना", "कमीने"],
    "fr": ["putain", "merde", "connard", "connasse", "salaud", "salope", "enculé", "encule", "bordel",
           "chier", "foutre", "foutu", "nique", "niquer", "enfoiré", "enfoire", "bâtard", "batard", "pute", "fdp",
           "ntm"],
    "ja": ["くそ", "クソ", "糞", "ちくしょう", "チクショウ", "畜生", "くたばれ", "ふざけんな", "ぶっ殺"],
}  # fmt: skip

_BLEEP_RE = re.compile(r"\[\s*_+\s*\]|\[\s*(?:bleep|beep|censored|expletive|pitido|bip|삐|ピー)\s*\]", re.IGNORECASE)
_MASK_CHARS = "*＊●○■□×#"
_MASK_RUN_RE = re.compile(f"[{re.escape(_MASK_CHARS)}]+")
_PAUSE_MARKS = (",", "，", "、", "…", "...", "。", ".", "?", "!", "？", "！")
_STUTTER_RE = re.compile(r"^(\w{1,3})-(\w+)$")


@dataclass
class Tok:
    key: str
    masked: bool = False
    pause_after: bool = False
    stutter: bool = False  # "b-but" written with a hyphen


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("P", "S")


def _key(piece: str) -> str:
    return piece.strip("".join(ch for ch in set(piece) if _is_punct(ch) and ch not in "'-")).strip("'-").lower()


def _prepare(text: str) -> str:
    text = _BLEEP_RE.sub(f" {MASK} ", nfkc(text or ""))
    return strip_bracket_tags(text)


def _space_tokens(text: str) -> list[Tok]:
    out: list[Tok] = []
    pieces = _prepare(text).split()
    for piece in pieces:
        if piece == MASK:
            out.append(Tok("<masked>", masked=True))
            continue
        core = _key(piece)
        if not core:
            if out and piece.startswith(_PAUSE_MARKS):
                out[-1].pause_after = True
            continue
        masked = any(ch in _MASK_CHARS for ch in piece) and len(piece) >= 2
        tok = Tok("<masked>" if masked else core, masked=masked, pause_after=piece.endswith(_PAUSE_MARKS))
        m = _STUTTER_RE.match(core)
        if m and m.group(2).startswith(m.group(1)):
            tok.key, tok.stutter = m.group(2), True
        out.append(tok)
    return out


def _ja_tokens(text: str) -> list[Tok]:
    from clipbench.scoring.normalize import _tagger

    text = _MASK_RUN_RE.sub(f" {MASK} ", _prepare(text))
    fillers = sorted(FILLERS["ja"] | PAUSE_FILLERS["ja"], key=len, reverse=True)
    out: list[Tok] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        for w in _tagger()("".join(buf)):
            s = w.surface
            if s == MASK:
                out.append(Tok("<masked>", masked=True))
            elif all(_is_punct(ch) for ch in s):
                if out:
                    out[-1].pause_after = True
            else:
                out.append(Tok(s))
        buf.clear()

    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            flush()
            if out:
                out[-1].pause_after = True
            i += 1
            continue
        hit = None
        if not buf or _is_punct(buf[-1]) or buf[-1].isspace():  # a filler starts a phrase
            for f in fillers:
                if text.startswith(f, i):
                    nxt = text[i + len(f)] if i + len(f) < len(text) else ""
                    if f in FILLERS["ja"] or nxt == "" or nxt.isspace() or _is_punct(nxt) or nxt == "ー":
                        hit = f
                        break
        if hit:
            flush()
            j = i + len(hit)
            while j < len(text) and text[j] == "ー":
                j += 1
            out.append(Tok(hit, pause_after=j >= len(text) or _is_punct(text[j])))
            i = j
            continue
        buf.append(ch)
        i += 1
    flush()
    # Japanese stutter: a one character token, a pause, then a word starting with that character (わ、わたし)
    for k in range(len(out) - 1):
        a, b = out[k], out[k + 1]
        if len(a.key) == 1 and a.pause_after and b.key.startswith(a.key) and b.key != a.key:
            b.stutter = True
    return out


def style_tokens(text: str, language: str) -> list[Tok]:
    lang = language.split("-")[0].lower()
    return _ja_tokens(text) if lang == "ja" else _space_tokens(text)


def is_filler(toks: list[Tok], i: int, language: str) -> bool:
    lang = language.split("-")[0].lower()
    t = toks[i]
    if t.masked:
        return False
    if t.key in FILLERS.get(lang, set()):
        return True
    if t.key in PAUSE_FILLERS.get(lang, set()):
        nxt_filler = i + 1 < len(toks) and toks[i + 1].key in FILLERS.get(lang, set())
        return t.pause_after or nxt_filler
    for phrase in PAUSE_FILLER_PHRASES.get(lang, []):
        for start in range(max(0, i - len(phrase) + 1), i + 1):
            end = start + len(phrase)
            if end > len(toks) or tuple(x.key for x in toks[start:end]) != phrase:
                continue
            nxt_filler = end < len(toks) and toks[end].key in FILLERS.get(lang, set())
            if toks[end - 1].pause_after or nxt_filler:
                return True
    return False


def is_profane(tok: Tok, language: str) -> bool:
    lang = language.split("-")[0].lower()
    stems = PROFANITY.get(lang, [])
    if lang in ("ja", "ko"):
        return any(s in tok.key for s in stems)
    return any(tok.key.startswith(s) for s in stems)


def repetitions(toks: list[Tok], language: str) -> list[int]:
    """Indices of tokens that repeat the previous token ("I I"), or are hyphen stutters ("b-but")."""
    out = []
    for i, t in enumerate(toks):
        if t.masked or is_filler(toks, i, language):
            continue
        if t.stutter or (i > 0 and toks[i - 1].key == t.key and not toks[i - 1].masked):
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Per clip
# ---------------------------------------------------------------------------

STYLE_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "category", "conditions", "minutes",
    "ref_fillers", "fillers_kept", "hyp_fillers", "filler_keep_rate", "hyp_fillers_per_min",
    "ref_repetitions", "repetitions_kept", "hyp_repetitions", "repetition_keep_rate",
    "ref_profanity", "prof_written", "prof_masked", "prof_dropped", "prof_replaced",
    "ref_bleeps", "bleep_masked", "bleep_dropped", "bleep_other", "hyp_masked", "hyp_profanity",
]  # fmt: skip


def clip_style(ref_text: str, hyp_text: str, language: str, minutes: float | None) -> dict[str, Any]:
    r, h = style_tokens(ref_text, language), style_tokens(hyp_text, language)
    # profane and masked words share one alignment key, so a masked word lines up with the word it hides
    def akey(t: Tok) -> str:
        return "<prof>" if t.masked or is_profane(t, language) else t.key

    a = align([akey(t) for t in r], [akey(t) for t in h])
    to_hyp: dict[int, tuple[str, int]] = {o.ref_index: (o.op, o.hyp_index) for o in a.ops if o.op in ("equal", "sub")}
    r_fill = [i for i in range(len(r)) if is_filler(r, i, language)]
    h_fill = [i for i in range(len(h)) if is_filler(h, i, language)]
    h_fill_set = set(h_fill)
    kept_fill = sum(1 for i in r_fill if i in to_hyp and to_hyp[i][1] in h_fill_set)
    r_rep = repetitions(r, language)
    h_rep = set(repetitions(h, language))
    kept_rep = 0
    for i in r_rep:
        cur, prev = to_hyp.get(i), to_hyp.get(i - 1)
        if r[i].stutter:
            kept_rep += bool(cur and h[cur[1]].stutter)
        else:
            kept_rep += bool(cur and prev and cur[0] == "equal" and prev[0] == "equal" and cur[1] in h_rep)
    prof = {"written": 0, "masked": 0, "dropped": 0, "replaced": 0}
    bleep = {"masked": 0, "dropped": 0, "other": 0}
    n_prof = n_bleep = 0
    for i, t in enumerate(r):
        target = to_hyp.get(i)
        ht = h[target[1]] if target else None
        if t.masked:
            n_bleep += 1
            bleep["dropped" if ht is None else "masked" if ht.masked else "other"] += 1
        elif is_profane(t, language):
            n_prof += 1
            if ht is None:
                prof["dropped"] += 1
            elif ht.masked:
                prof["masked"] += 1
            elif is_profane(ht, language):
                prof["written"] += 1
            else:
                prof["replaced"] += 1
    per_min = (len(h_fill) / minutes) if minutes else None
    return {
        "minutes": minutes,
        "ref_fillers": len(r_fill), "fillers_kept": kept_fill, "hyp_fillers": len(h_fill),
        "filler_keep_rate": kept_fill / len(r_fill) if r_fill else None,
        "hyp_fillers_per_min": per_min,
        "ref_repetitions": len(r_rep), "repetitions_kept": kept_rep, "hyp_repetitions": len(h_rep),
        "repetition_keep_rate": kept_rep / len(r_rep) if r_rep else None,
        "ref_profanity": n_prof, "prof_written": prof["written"], "prof_masked": prof["masked"],
        "prof_dropped": prof["dropped"], "prof_replaced": prof["replaced"],
        "ref_bleeps": n_bleep, "bleep_masked": bleep["masked"], "bleep_dropped": bleep["dropped"],
        "bleep_other": bleep["other"],
        "hyp_masked": sum(t.masked for t in h), "hyp_profanity": sum(is_profane(t, language) for t in h),
    }  # fmt: skip


def style_frame(run_id: str, settings: Settings) -> pd.DataFrame | None:
    data = load_run(run_id, settings)
    if data is None:
        return None
    records = []
    for row, ref, hyp in data.pairs():
        seconds = getattr(row, "duration_s", None)
        if seconds is None or pd.isna(seconds):
            seconds = getattr(row, "audio_seconds", None)
        minutes = float(seconds) / 60 if seconds is not None and not pd.isna(seconds) and seconds > 0 else None
        rec = clip_style(ref.text, hyp, row.language, minutes)
        rec.update(
            provider=row.provider, model_id=row.model_id, video_id=row.video_id, tier=row.tier,
            language=row.language, category=ref.category, conditions=json.dumps(ref.all_conditions),
        )  # fmt: skip
        records.append(rec)
    return pd.DataFrame(records, columns=STYLE_COLUMNS)


def _label(rate: float, n: int) -> str | None:
    if n < 5 or np.isnan(rate):
        return None
    return "keeps" if rate >= 0.8 else "drops" if rate <= 0.2 else "mixed"


def _metrics(seed: int):
    def fn(g: pd.DataFrame) -> dict[str, Any]:
        fk = rate_ci(g["fillers_kept"], g["ref_fillers"], seed)
        rk = rate_ci(g["repetitions_kept"], g["ref_repetitions"], seed)
        minutes = float(g["minutes"].fillna(0).sum())
        n_prof = int(g["ref_profanity"].sum())
        shares = {k: (float(g[f"prof_{k}"].sum()) / n_prof if n_prof else np.nan)
                  for k in ("written", "masked", "dropped", "replaced")}  # fmt: skip
        prof_style = max(shares, key=lambda k: shares[k]) if n_prof >= 5 else None
        n_fill, n_rep = int(g["ref_fillers"].sum()), int(g["ref_repetitions"].sum())
        return {
            "minutes": minutes,
            "ref_fillers": n_fill, "filler_keep_rate": fk[0], "filler_ci_low": fk[1], "filler_ci_high": fk[2],
            "hyp_fillers_per_min": float(g["hyp_fillers"].sum() / minutes) if minutes else np.nan,
            "filler_style": _label(fk[0], n_fill),
            "ref_repetitions": n_rep, "repetition_keep_rate": rk[0], "repetition_ci_low": rk[1],
            "repetition_ci_high": rk[2], "repetition_style": _label(rk[0], n_rep),
            "ref_profanity": n_prof, **{f"prof_{k}_share": v for k, v in shares.items()},
            "profanity_style": prof_style,
            "ref_bleeps": int(g["ref_bleeps"].sum()),
            "bleep_masked_share": float(g["bleep_masked"].sum() / g["ref_bleeps"].sum()) if g["ref_bleeps"].sum() else np.nan,
            "hyp_masked_per_min": float(g["hyp_masked"].sum() / minutes) if minutes else np.nan,
        }  # fmt: skip

    return fn


def style_aggregates(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return grouped(df, _metrics(seed))


def score_style(run_id: str, settings: Settings | None = None) -> Path | None:
    s = settings or get_settings()
    df = style_frame(run_id, s)
    if df is None:
        return None
    out = results_path(s, run_id, "style.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    style_aggregates(df, s.sample_seed).to_parquet(out.with_name("style_aggregates.parquet"), index=False)
    return out
