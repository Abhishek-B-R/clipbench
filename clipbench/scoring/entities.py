"""Proper nouns and brands (B17): accuracy on the reference's `entity_spans`.

Per entity span: is it written correctly in the hypothesis (exact match after the language's metric
normaliser, spaces ignored), a lenient character level score, and whether it appears anywhere in the
hypothesis. Split by entity type and by the run option `keyterms` (on or off, from the row's `options`),
with a paired comparison when the same clips were run both ways.

Writes results/<run_id>/entities.parquet and entities_aggregates.parquet. Numbers only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.scoring.formatting import aligned_hyp_span, span_alignment
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import loosen_latin, primary_metric, transliterate_devanagari
from clipbench.scoring.quality_io import grouped, load_run, missing, rate_ci, results_path
from clipbench.scoring.stats import paired_bootstrap

ENTITY_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "category", "conditions", "keyterms",
    "entity_index", "entity_type", "ref_chars", "found", "exact", "lenient", "anywhere",
]  # fmt: skip


def keyterms_on(options: Any) -> bool:
    """True when the row ran with a keyterm list (`options` is the row's json string)."""
    if missing(options) or options == "":
        return False
    try:
        parsed = json.loads(options) if isinstance(options, str) else options
    except ValueError:
        return False
    return bool(isinstance(parsed, dict) and parsed.get("keyterms"))


def _canon(language: str) -> Callable[[str], str]:
    """Normalised, spaces removed. Hindi additionally compares in loose romanisation."""
    spec = primary_metric(language)

    def canon(text: str) -> str:
        return "".join(spec.normalize(text or "").split())

    return canon


def _hi_roman(text: str, language: str) -> str:
    spec = primary_metric(language)
    return "".join(loosen_latin(transliterate_devanagari(spec.normalize(text or ""))).split())


def lenient_score(ref: str, hyp: str) -> float:
    """1 - character edit distance / reference characters, floored at 0."""
    if not ref:
        return float(hyp == "")
    return max(0.0, 1.0 - align(list(ref), list(hyp)).errors / len(ref))


def entity_scores(ref_text: str, hyp_text: str, spans, language: str) -> list[dict[str, Any]]:
    lang = language.split("-")[0].lower()
    canon = _canon(lang)
    r_units, h_units, ops = span_alignment(ref_text, hyp_text, lang)
    hyp_all = canon(hyp_text)
    hyp_all_roman = _hi_roman(hyp_text, lang) if lang == "hi" else ""
    out = []
    for i, sp in enumerate(spans):
        ref_piece = ref_text[sp.start : sp.end]
        ref_c = canon(ref_piece)
        rng = aligned_hyp_span(r_units, h_units, ops, sp.start, sp.end)
        hyp_piece = hyp_text[rng[0] : rng[1]] if rng else ""
        hyp_c = canon(hyp_piece)
        # the aligned piece is whole words, so also compare against the reference widened to whole words
        inside = [u for u in r_units if u.start < sp.end and u.end > sp.start]
        wide_c = canon(ref_text[inside[0].piece_start : inside[-1].piece_end]) if inside else ref_c
        exact = bool(ref_c) and hyp_c in (ref_c, wide_c)
        lenient = lenient_score(ref_c, hyp_c)
        anywhere = bool(ref_c) and ref_c in hyp_all
        if lang == "hi":
            rr, hr = _hi_roman(ref_piece, lang), _hi_roman(hyp_piece, lang)
            exact = exact or (bool(rr) and rr == hr)
            lenient = max(lenient, lenient_score(rr, hr))
            anywhere = anywhere or (bool(rr) and rr in hyp_all_roman)
        out.append(
            {
                "entity_index": i,
                "entity_type": sp.type,
                "ref_chars": len(ref_c),
                "found": rng is not None,
                "exact": exact,
                "lenient": lenient,
                "anywhere": anywhere,
            }
        )
    return out


def entities_frame(run_id: str, settings: Settings) -> pd.DataFrame | None:
    data = load_run(run_id, settings)
    if data is None:
        return None
    records = []
    for row, ref, hyp in data.pairs():
        if not ref.entity_spans:
            continue
        base = {
            "provider": row.provider, "model_id": row.model_id, "video_id": row.video_id, "tier": row.tier,
            "language": row.language, "category": ref.category, "conditions": json.dumps(ref.all_conditions),
            "keyterms": keyterms_on(getattr(row, "options", None)),
        }  # fmt: skip
        for rec in entity_scores(ref.text, hyp, ref.entity_spans, row.language):
            records.append({**base, **rec})
    return pd.DataFrame(records, columns=ENTITY_COLUMNS)


def _per_clip(group: pd.DataFrame) -> pd.DataFrame:
    return group.groupby("video_id").agg(
        n=("exact", "size"),
        exact=("exact", "sum"),
        lenient=("lenient", "sum"),
        anywhere=("anywhere", "sum"),
    )


def _metrics(seed: int):
    def fn(group: pd.DataFrame) -> dict[str, Any]:
        clips = _per_clip(group)
        ex = rate_ci(clips["exact"], clips["n"], seed)
        le = rate_ci(clips["lenient"], clips["n"], seed)
        return {
            "n_entities": int(clips["n"].sum()),
            "exact_acc": ex[0], "exact_ci_low": ex[1], "exact_ci_high": ex[2],
            "lenient_acc": le[0], "lenient_ci_low": le[1], "lenient_ci_high": le[2],
            "anywhere_rate": float(clips["anywhere"].sum() / clips["n"].sum()),
            "found_rate": float(group["found"].mean()),
        }  # fmt: skip

    return fn


def entities_aggregates(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Per provider, tier, language and keyterms: slices all, condition, category and entity_type."""
    if df is None or df.empty:
        return pd.DataFrame()
    parts = [grouped(df, _metrics(seed), extra_keys=["keyterms"])]
    for etype, sub in df.groupby("entity_type", sort=True):
        agg = grouped(sub.assign(conditions="[]", category=None), _metrics(seed), extra_keys=["keyterms"])
        parts.append(agg[agg["slice_kind"] == "all"].assign(slice_kind="entity_type", slice=etype))
    return pd.concat([p for p in parts if not p.empty], ignore_index=True)


def keyterms_comparison(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Paired keyterms on vs off, per provider and tier, over entities scored both ways.

    Pairs are (provider, tier, video_id, entity_index) present with keyterms on and off (from one run or
    several). Clip bootstrap of the miss rate: diff = miss rate with keyterms minus without; verdict
    `a_better` means keyterms helped."""
    cols = ["provider", "tier", "language", "n_clips", "n_entities", "exact_off", "exact_on", "diff", "ci_low",
            "ci_high", "p_value", "verdict"]  # fmt: skip
    if df is None or df.empty or df["keyterms"].nunique() < 2:
        return pd.DataFrame(columns=cols)
    key = ["provider", "tier", "language", "video_id", "entity_index"]
    on = df[df["keyterms"]].drop_duplicates(key).set_index(key)["exact"]
    off = df[~df["keyterms"]].drop_duplicates(key).set_index(key)["exact"]
    both = pd.concat({"on": on, "off": off}, axis=1, join="inner").reset_index()
    records = []
    for (provider, tier, language), g in both.groupby(["provider", "tier", "language"], sort=True):
        clips = g.groupby("video_id").agg(n=("on", "size"), on=("on", "sum"), off=("off", "sum"))
        n = clips["n"].to_numpy(dtype=float)
        res = paired_bootstrap(n - clips["on"].to_numpy(dtype=float), n - clips["off"].to_numpy(dtype=float), n,
                               seed=seed)  # fmt: skip
        records.append(
            {
                "provider": provider, "tier": tier, "language": language, "n_clips": int(len(clips)),
                "n_entities": int(n.sum()), "exact_off": float(clips["off"].sum() / n.sum()),
                "exact_on": float(clips["on"].sum() / n.sum()), "diff": res.diff, "ci_low": res.ci_low,
                "ci_high": res.ci_high, "p_value": res.p_value, "verdict": res.verdict,
            }
        )  # fmt: skip
    return pd.DataFrame(records, columns=cols)


def counterpart_frames(run_id: str, settings: Settings, language: str) -> list[pd.DataFrame]:
    """entities.parquet of other runs in the same language (for keyterms on/off pairs)."""
    out = []
    for path in sorted(settings.results_dir.glob("*/entities.parquet")):
        if path.parent.name == run_id:
            continue
        try:
            df = pd.read_parquet(path)
        except (OSError, ValueError):
            continue
        if not df.empty and set(df["language"].astype(str).str.split("-").str[0]) == {language}:
            out.append(df)
    return out


def score_entities(run_id: str, settings: Settings | None = None) -> Path | None:
    s = settings or get_settings()
    df = entities_frame(run_id, s)
    if df is None:
        return None
    out = results_path(s, run_id, "entities.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    entities_aggregates(df, s.sample_seed).to_parquet(out.with_name("entities_aggregates.parquet"), index=False)
    return out


def export_payload(run_id: str, settings: Settings, df: pd.DataFrame, agg: pd.DataFrame) -> dict[str, Any] | None:
    from clipbench.scoring.quality_io import frame_records

    if df is None or df.empty:
        return None
    language = str(df["language"].iloc[0]).split("-")[0]
    pool = pd.concat([df, *counterpart_frames(run_id, settings, language)], ignore_index=True)
    comparison = keyterms_comparison(pool, settings.sample_seed)
    comparison = comparison[comparison["provider"].isin(set(df["provider"]))] if not comparison.empty else comparison
    return {
        "aggregates": frame_records(agg) or [],
        "keyterms_comparison": frame_records(comparison) or [],
        "n_entities": int(len(df.drop_duplicates(["video_id", "entity_index"]))),
        "types": sorted({str(t) for t in df["entity_type"]}),
        "keyterms_runs": bool(np.any(df["keyterms"])),
    }
