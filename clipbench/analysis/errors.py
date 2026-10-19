"""Error analysis for one run: what each provider gets wrong.

Reads hypothesis text from data/runs/<run_id>/texts.parquet and the reference snapshot from
data/runs/<run_id>/refs.parquet, aligns normalised ref vs hyp tokens of the primary metric and writes
results/<run_id>/errors.json (schema in docs/INTERFACES.md) plus data/rereview.json.

Rules:
- Only repeat 0 is analysed (the main pass).
- When a clip has a verified reference it is used, otherwise the creator reference.
- Unspaced languages (ja, ko primary metrics are character based): consecutive non-matching
  alignment ops that contain at least one substitution are grouped into one span, so a pair reads
  like "kanji -> kana" instead of single characters. Spans longer than 8 characters are dropped
  (they are garbled regions, not confusions, and would copy too much caption text).
- Spaced languages (es): each substituted word is one pair.
- all_fail: every provider with a result on the clip has primary error rate above ALL_FAIL_THRESHOLD,
  and at least 2 providers have a result. These usually mean a bad reference, so they go to
  data/rereview.json (overwritten each run, sorted list of video ids).
- Examples are short: at most 4 words (8 characters for unspaced languages) of ref and hyp around
  the error, and never the whole caption (always fewer tokens than the reference).
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from clipbench.config import Settings, get_settings

ALL_FAIL_THRESHOLD = 0.5
TOP_SUBSTITUTIONS = 20
WORST_CLIPS = 10
EXAMPLES_PER_PROVIDER = 3
MAX_SNIPPET_WORDS = 4
MAX_SNIPPET_CHARS = 8
MAX_SPAN_CHARS = 8

UNSPACED_LANGUAGES = {"ja", "ko"}
FALLBACK_PRIMARY = {"ja": "cer", "es": "wer", "ko": "cer_nospace"}

EQ, SUB, DEL, INS = "eq", "sub", "del", "ins"
_OP_NAMES = {
    EQ: {"eq", "equal", "match", "hit", "ok", "=", "c", "correct"},
    SUB: {"sub", "substitute", "substitution", "replace", "s"},
    DEL: {"del", "delete", "deletion", "d"},
    INS: {"ins", "insert", "insertion", "i"},
}


# ---------------------------------------------------------------- scoring adapters


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def primary_metric_name(language: str) -> str:
    try:
        from clipbench.scoring.normalize import metrics_for

        for spec in metrics_for(language):
            if _get(spec, "is_primary"):
                return str(_get(spec, "name"))
    except Exception:  # noqa: BLE001 - scoring may not exist yet
        pass
    return FALLBACK_PRIMARY.get(language, "wer")


def _fallback_tokens(text: str, language: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in text)
    if language in UNSPACED_LANGUAGES:
        return [c for c in text if not c.isspace()]
    return text.split()


def primary_tokenizer(language: str) -> Callable[[str], list[str]]:
    """Tokenizer of the primary metric, from clipbench.scoring.normalize when available."""
    try:
        from clipbench.scoring import normalize as norm

        spec = next(s for s in norm.metrics_for(language) if _get(s, "is_primary"))
        tok = _get(spec, "tokenizer")
        if callable(tok):
            return lambda text: [str(t) for t in tok(text or "")]
        if isinstance(tok, str):
            fn = getattr(norm, "tokenize", None)
            if callable(fn):
                return lambda text: [str(t) for t in fn(text or "", tok)]
    except Exception:  # noqa: BLE001
        pass
    return lambda text: _fallback_tokens(text, language)


def _canon_op(op: Any) -> str:
    name = str(op).lower()
    for canon, names in _OP_NAMES.items():
        if name in names:
            return canon
    return SUB


def get_align() -> Callable[[Sequence[str], Sequence[str]], Any]:
    from clipbench.scoring.metrics import align

    return align


def aligned_ops(align_fn, ref: list[str], hyp: list[str]) -> list[tuple[str, str | None, str | None]]:
    res = align_fn(ref, hyp)
    ops = _get(res, "ops")
    if ops is None and isinstance(res, tuple | list):
        ops = res[-1]
    out = []
    for item in ops or []:
        op, r, h = item[0], item[1], item[2]
        out.append((_canon_op(op), r if r not in ("", None) else None, h if h not in ("", None) else None))
    return out


# ---------------------------------------------------------------- core


@dataclass
class ClipAlignment:
    provider: str
    video_id: str
    language: str
    tier: str
    ref_len: int
    errors: int
    ops: list[tuple[str, str | None, str | None]]

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.ref_len, 1)


def _units(tokens: list[str], unspaced: bool) -> int:
    return sum(len(t) for t in tokens) if unspaced else len(tokens)


def error_spans(ops, unspaced: bool) -> list[tuple[int, int]]:
    """Index ranges [i, j) of error runs that are substitutions."""
    spans = []
    if not unspaced:
        return [(i, i + 1) for i, o in enumerate(ops) if o[0] == SUB]
    i = 0
    while i < len(ops):
        if ops[i][0] == EQ:
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j][0] != EQ:
            j += 1
        if any(o[0] == SUB for o in ops[i:j]):
            spans.append((i, j))
        i = j
    return spans


def span_pair(ops, span, unspaced: bool) -> tuple[str, str]:
    joiner = "" if unspaced else " "
    i, j = span
    ref = joiner.join(o[1] for o in ops[i:j] if o[1] is not None and o[0] != INS)
    hyp = joiner.join(o[2] for o in ops[i:j] if o[2] is not None and o[0] != DEL)
    return ref, hyp


def snippet(ops, span, unspaced: bool, ref_len: int) -> tuple[str, str] | None:
    """Short ref/hyp context around an error span, never the full reference."""
    limit = MAX_SNIPPET_CHARS if unspaced else MAX_SNIPPET_WORDS
    ref_tokens_total = _units([o[1] for o in ops if o[1] is not None and o[0] != INS], unspaced)
    limit = min(limit, ref_tokens_total - 1)
    if limit <= 0:
        return None

    def counts(lo, hi):
        window = ops[lo:hi]
        r = [o[1] for o in window if o[1] is not None and o[0] != INS]
        h = [o[2] for o in window if o[2] is not None and o[0] != DEL]
        return _units(r, unspaced), _units(h, unspaced)

    lo, hi = span
    while hi > lo + 1 and max(counts(lo, hi)) > limit:
        hi -= 1
    if max(counts(lo, hi)) > limit:
        return None
    grew = True
    while grew:
        grew = False
        for nlo, nhi in ((lo - 1, hi), (lo, hi + 1)):
            if nlo < 0 or nhi > len(ops):
                continue
            if max(counts(nlo, nhi)) <= limit:
                lo, hi = nlo, nhi
                grew = True
    joiner = "" if unspaced else " "
    window = ops[lo:hi]
    ref = joiner.join(o[1] for o in window if o[1] is not None and o[0] != INS)
    hyp = joiner.join(o[2] for o in window if o[2] is not None and o[0] != DEL)
    return ref, hyp


def _missing(x: Any) -> bool:
    import pandas as pd

    return x is None or (not isinstance(x, str) and bool(pd.isna(x)))


def _read_parquet(path: Path):
    import pandas as pd

    return pd.read_parquet(path) if path.exists() else None


def _pick_refs(refs) -> dict[str, tuple[str, str]]:
    """video_id -> (tier, ref_text), verified preferred over creator."""
    out: dict[str, tuple[str, str]] = {}
    for row in refs.itertuples(index=False):
        tier = str(row.tier)
        if row.video_id in out and out[row.video_id][0] == "verified":
            continue
        if _missing(row.ref_text):
            continue
        out[row.video_id] = (tier, str(row.ref_text))
    return out


def _clip_languages(settings: Settings, run_id: str) -> tuple[dict[str, str], Any]:
    rows = _read_parquet(settings.runs_dir / run_id / "rows.parquet")
    langs: dict[str, str] = {}
    if rows is not None and "language" in rows:
        langs = dict(zip(rows["video_id"], rows["language"], strict=False))
    return langs, rows


def align_run(run_id: str, settings: Settings | None = None, align_fn=None) -> list[ClipAlignment]:
    settings = settings or get_settings()
    text_dir = settings.runs_text_dir / run_id
    texts = _read_parquet(text_dir / "texts.parquet")
    refs = _read_parquet(text_dir / "refs.parquet")
    if texts is None or refs is None:
        raise FileNotFoundError(f"missing texts.parquet or refs.parquet in {text_dir}")
    align_fn = align_fn or get_align()
    ref_map = _pick_refs(refs)
    langs, rows = _clip_languages(settings, run_id)
    ok_pairs = None
    if rows is not None and {"status", "repeat"} <= set(rows.columns):
        main = rows[(rows["repeat"] == 0) & (rows["status"] == "ok")]
        ok_pairs = set(zip(main["provider"], main["video_id"], strict=False))
    if "repeat" in texts:
        texts = texts[texts["repeat"] == 0]

    tokenizers: dict[str, Callable[[str], list[str]]] = {}
    out: list[ClipAlignment] = []
    for row in texts.itertuples(index=False):
        if row.video_id not in ref_map or _missing(row.hyp_text):
            continue
        if ok_pairs is not None and (row.provider, row.video_id) not in ok_pairs:
            continue
        lang = str(langs.get(row.video_id) or settings.language).split("-")[0].lower()
        tok = tokenizers.setdefault(lang, primary_tokenizer(lang))
        tier, ref_text = ref_map[row.video_id]
        ref, hyp = tok(ref_text), tok(str(row.hyp_text))
        ops = aligned_ops(align_fn, ref, hyp)
        errors = sum(1 for o in ops if o[0] != EQ)
        out.append(ClipAlignment(str(row.provider), str(row.video_id), lang, tier, len(ref), errors, ops))
    return out


def _score_rates(settings: Settings, run_id: str) -> dict[tuple[str, str], float]:
    scores = _read_parquet(settings.results_dir / run_id / "scores.parquet")
    if scores is None or "is_primary" not in scores:
        return {}
    prim = scores[scores["is_primary"].astype(bool)]
    return {
        (p, v): float(e)
        for p, v, e in zip(prim["provider"], prim["video_id"], prim["error_rate"], strict=False)
    }


def analyse(alignments: list[ClipAlignment], rates: dict[tuple[str, str], float] | None = None) -> dict:
    rates = dict(rates or {})
    for a in alignments:
        rates.setdefault((a.provider, a.video_id), a.error_rate)

    present = {(a.provider, a.video_id) for a in alignments}
    by_clip: dict[str, dict[str, float]] = defaultdict(dict)
    for (p, v), r in rates.items():
        if (p, v) in present:
            by_clip[v][p] = r
    providers = sorted({a.provider for a in alignments})

    subs: dict[str, Counter] = {p: Counter() for p in providers}
    first_example: dict[tuple[str, str, str], dict] = {}
    for a in alignments:
        unspaced = a.language in UNSPACED_LANGUAGES
        for span in error_spans(a.ops, unspaced):
            ref, hyp = span_pair(a.ops, span, unspaced)
            if not ref or not hyp:
                continue
            if unspaced and (len(ref) > MAX_SPAN_CHARS or len(hyp) > MAX_SPAN_CHARS):
                continue
            if not unspaced and (
                len(ref.split()) > MAX_SNIPPET_WORDS or len(hyp.split()) > MAX_SNIPPET_WORDS
            ):
                continue
            subs[a.provider][(ref, hyp)] += 1
            key = (a.provider, ref, hyp)
            if key not in first_example:
                snip = snippet(a.ops, span, unspaced, a.ref_len)
                if snip:
                    first_example[key] = {
                        "provider": a.provider,
                        "video_id": a.video_id,
                        "ref_snippet": snip[0],
                        "hyp_snippet": snip[1],
                    }

    result: dict[str, Any] = {"providers": {}, "all_fail": [], "examples": []}
    for p in providers:
        top = subs[p].most_common(TOP_SUBSTITUTIONS)
        worst = []
        for v, per in by_clip.items():
            if p not in per:
                continue
            others = [r for q, r in per.items() if q != p]
            if not others:
                continue
            med = median(others)
            worst.append({"video_id": v, "error_rate": round(per[p], 4), "others_median": round(med, 4)})
        worst.sort(key=lambda w: (-(w["error_rate"] - w["others_median"]), w["video_id"]))
        result["providers"][p] = {
            "top_substitutions": [[r, h, n] for (r, h), n in top],
            "worst_clips": worst[:WORST_CLIPS],
        }
        used_clips: set[str] = set()
        n = 0
        for (r, h), _ in top:
            ex = first_example.get((p, r, h))
            if ex and ex["video_id"] not in used_clips:
                result["examples"].append(ex)
                used_clips.add(ex["video_id"])
                n += 1
            if n >= EXAMPLES_PER_PROVIDER:
                break

    result["all_fail"] = sorted(
        v for v, per in by_clip.items() if len(per) >= 2 and all(r > ALL_FAIL_THRESHOLD for r in per.values())
    )
    result["all_fail_threshold"] = ALL_FAIL_THRESHOLD
    return result


def _add_clip_slices(result: dict, settings: Settings, run_id: str) -> None:
    """Label worst clips with their content category and audio condition (v2 slices), when scored."""
    scores = _read_parquet(settings.results_dir / run_id / "scores.parquet")
    if scores is None or not {"category", "audio_condition"} <= set(scores.columns):
        return
    info = {
        str(r.video_id): (None if _missing(r.category) else str(r.category),
                          None if _missing(r.audio_condition) else str(r.audio_condition))
        for r in scores.drop_duplicates("video_id").itertuples(index=False)
    }  # fmt: skip
    for prov in result["providers"].values():
        for w in prov["worst_clips"]:
            w["category"], w["audio_condition"] = info.get(w["video_id"], (None, None))
    fails = Counter(info.get(v, (None, None))[1] for v in result["all_fail"])
    result["all_fail_by_audio_condition"] = {str(k): n for k, n in fails.items() if k}


def run_errors(run_id: str, settings: Settings | None = None, align_fn=None) -> dict:
    settings = settings or get_settings()
    alignments = align_run(run_id, settings, align_fn=align_fn)
    result = analyse(alignments, _score_rates(settings, run_id))
    _add_clip_slices(result, settings, run_id)
    out_dir = settings.results_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "errors.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "rereview.json").write_text(json.dumps(result["all_fail"], indent=2))
    return result
