"""Score a run: per clip metrics, aggregates with CIs, paired comparisons and stability.

Inputs (see docs/INTERFACES.md):
  results/runs/<run_id>/rows.parquet, data/runs/<run_id>/texts.parquet, data/runs/<run_id>/refs.parquet
Outputs in results/<run_id>/:
  scores.parquet, aggregates.parquet, paired.parquet, stability.parquet
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import (
    CATEGORIES,
    CONDITIONS,
    TAGS,
    AlternativeSpan,
    CodeSwitchSpan,
    NonSpeechSpan,
    Word,
)
from clipbench.scoring.codeswitch import codeswitch_error
from clipbench.scoring.lattice import Lattice, build_lattice, lattice_score
from clipbench.scoring.metrics import align, hallucination
from clipbench.scoring.normalize import MetricSpec, metrics_for
from clipbench.scoring.readiness import readiness
from clipbench.scoring.stats import (
    bootstrap_ci,
    cluster_bootstrap_ci,
    mean_clip_rate,
    paired_bootstrap,
    paired_cluster_bootstrap,
)
from clipbench.scoring.timing import nonspeech_hallucination, nonspeech_intervals, timing_error

log = logging.getLogger(__name__)

TOO_SMALL = 15  # slices with fewer clips are flagged too_small
DURATION_BUCKETS = ["<15s", "15-30s", "30-60s", "60-90s"]

SCORE_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "metric", "is_primary", "ref_len", "hits",
    "subs", "dels", "ins", "errors", "error_rate", "hallucinated_tokens", "hallucination_rate",
    "length_ratio", "long_output", "latency_ms", "cost_usd", "audio_seconds", "duration_s",
    "duration_bucket", "tags",
    # v2 (docs/V2.md)
    "timing_n_words", "timing_median_ms", "timing_p90_ms", "timing_within_200ms", "nonspeech_minutes",
    "halluc_nonspeech_words", "halluc_per_nonspeech_min", "punct_f1", "casing_acc", "codeswitch_ref_len",
    "codeswitch_errors", "codeswitch_error_rate", "category", "audio_condition",
    # counts behind the v2 rates, so aggregates can be corpus style
    "timing_n_within_200ms", "punct_tp", "punct_fp", "punct_fn", "casing_n", "casing_correct",
    # v3 A7: closest valid path through the reference lattice (METHODOLOGY.md)
    "error_rate_lattice", "lattice_ref_len", "lattice_errors", "lattice_hits", "lattice_subs", "lattice_dels",
    "lattice_ins",
    # v3 A4: audio conditions from the review UI (json list); music is also implied by audio_condition
    "conditions",
    # served from the result cache: its latency was measured in an earlier run, under that run's load
    "cached",
]  # fmt: skip
AGGREGATE_COLUMNS = [
    "provider", "model_id", "tier", "metric", "slice_kind", "slice", "n_clips", "error_rate", "ci_low",
    "ci_high", "too_small", "hallucination_rate", "long_output_rate", "cost_usd_total", "audio_hours",
    "cost_per_hour", "latency_p50", "latency_p95", "rtf",
    # v2
    "timing_n_clips", "timing_n_words", "timing_median_ms", "timing_p90_ms", "timing_within_200ms",
    "nonspeech_n_clips", "nonspeech_minutes", "halluc_nonspeech_words", "halluc_per_nonspeech_min",
    "readiness_n_clips", "punct_f1", "casing_acc", "codeswitch_n_clips", "codeswitch_ref_len",
    "codeswitch_errors", "codeswitch_error_rate",
    # v3 A7
    "error_rate_lattice", "lattice_ci_low", "lattice_ci_high",
    # research safeguards (METHODOLOGY.md, "Statistical assumptions and limitations"): channel-clustered
    # bootstrap CI as a sensitivity analysis, and the mean of per clip error rates as a secondary number
    "ci_low_cluster", "ci_high_cluster", "n_channels", "mean_clip_error_rate",
]  # fmt: skip
PAIRED_COLUMNS = [
    "tier", "metric", "slice_kind", "slice", "provider_a", "provider_b", "n_clips", "diff", "ci_low",
    "ci_high", "p_value", "verdict",
    # channel-clustered sensitivity analysis (never replaces the clip bootstrap)
    "ci_low_cluster", "ci_high_cluster", "p_value_cluster", "n_channels",
]  # fmt: skip
STABILITY_COLUMNS = ["provider", "video_id", "n_repeats", "text_disagreement", "latency_mean", "latency_std"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def duration_bucket(seconds: float | None) -> str | None:
    """'<15s', '15-30s', '30-60s', '60-90s'. Clips longer than 90 s fall in '60-90s'."""
    if seconds is None or pd.isna(seconds):
        return None
    if seconds < 15:
        return "<15s"
    if seconds < 30:
        return "15-30s"
    if seconds < 60:
        return "30-60s"
    return "60-90s"


def _missing(value: Any) -> bool:
    """None, NaN or pd.NA (pandas string columns use pd.NA)."""
    return value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value))


def _parse_tags(value: Any) -> list[str]:
    if _missing(value):
        return []
    if isinstance(value, list | tuple | np.ndarray):
        return [str(t) for t in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(t) for t in parsed] if isinstance(parsed, list) else []


def _parse_words(value: Any) -> list[Word] | None:
    if _missing(value) or value == "":
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        words = [Word.model_validate(w) for w in parsed or []]
    except (TypeError, ValueError):
        return None
    return words or None


def _num(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _text(value: Any) -> str:
    return "" if _missing(value) else str(value)


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# Per clip scores
# ---------------------------------------------------------------------------


def _field(row: Any, *names: str) -> Any:
    """First non-missing attribute among `names` (refs.parquet columns are optional in v2)."""
    for name in names:
        value = getattr(row, name, None)
        if not _missing(value):
            return value
    return None


def _parse_models(value: Any, model: type) -> list:
    if _missing(value) or value == "":
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return [model.model_validate(x) for x in list(parsed or [])]
    except (TypeError, ValueError):
        return []


def _bool(value: Any) -> bool:
    if _missing(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def audio_condition(tags: list[str], spans: list[NonSpeechSpan], reviewed: bool = True) -> str:
    """'music' if tagged background_music or any non-speech span of kind music; otherwise 'no_music' for a
    reviewed (verified) reference and 'unknown' for one nobody listened to (creator captions carry no music
    tag, so their silence about music says nothing)."""
    if "background_music" in tags or any(s.kind == "music" for s in spans):
        return "music"
    return "no_music" if reviewed else "unknown"


@dataclass
class _RefInfo:
    text: str
    words: list[Word] | None
    tags: list[str]
    category: str | None
    nonspeech: list[NonSpeechSpan]
    codeswitch: list[CodeSwitchSpan]
    punctuated: bool
    alternatives: list[AlternativeSpan] | None = None
    conditions: list[str] | None = None
    reviewed: bool = True  # a person checked this reference (verified tier)


def clip_conditions(conditions: list[str] | None, tags: list[str], spans: list[NonSpeechSpan]) -> list[str]:
    """Reviewer conditions plus the ones implied by tags and spans, in CONDITIONS order."""
    have = set(conditions or [])
    if audio_condition(tags, spans) == "music":
        have.add("music")
    implied = {"multiple_speakers": "multiple_speakers", "fast_speech": "fast_speech",
               "code_switching": "code_switching"}  # fmt: skip
    have |= {cond for tag, cond in implied.items() if tag in tags}
    return [c for c in CONDITIONS if c in have]


class _RefCache:
    """Parsed references, and tokenised ones so a slow normaliser (ja reading) runs once per clip and metric."""

    def __init__(self, refs: pd.DataFrame) -> None:
        self.refs: dict[tuple[str, str], _RefInfo] = {}
        for r in refs.itertuples(index=False):
            category = _field(r, "category")
            self.refs[(r.video_id, r.tier)] = _RefInfo(
                text=_text(r.ref_text),
                words=_parse_words(_field(r, "words_json", "words")),
                tags=_parse_tags(_field(r, "tags")),
                category=str(category) if category is not None else None,
                nonspeech=_parse_models(_field(r, "nonspeech_spans_json", "nonspeech_spans"), NonSpeechSpan),
                codeswitch=_parse_models(_field(r, "codeswitch_spans_json", "codeswitch_spans"), CodeSwitchSpan),
                punctuated=_bool(_field(r, "punctuated")),
                alternatives=_parse_models(_field(r, "alternatives_json", "alternatives"), AlternativeSpan),
                conditions=_parse_tags(_field(r, "conditions_json", "conditions")),
                reviewed=str(r.tier) == "verified",
            )
        self._tokens: dict[tuple[str, str, str], list[str]] = {}
        self._lattices: dict[tuple[str, str, str], Lattice] = {}

    def get(self, video_id: str, tier: str) -> _RefInfo | None:
        return self.refs.get((video_id, tier))

    def tokens(self, video_id: str, tier: str, spec: MetricSpec) -> list[str]:
        key = (video_id, tier, spec.name)
        if key not in self._tokens:
            self._tokens[key] = spec.tokenizer(self.refs[(video_id, tier)].text)
        return self._tokens[key]

    def lattice(self, video_id: str, tier: str, spec: MetricSpec, language: str) -> Lattice:
        key = (video_id, tier, spec.name)
        if key not in self._lattices:
            ref = self.refs[(video_id, tier)]
            self._lattices[key] = build_lattice(ref.text, ref.alternatives, spec, language)
        return self._lattices[key]


def _clip_v2(
    ref: _RefInfo,
    hyp_text: str,
    hyp_words: list[Word] | None,
    language: str,
    duration_s: float,
    timed_provider: bool = False,
) -> dict:
    """v2 metrics that do not depend on the metric: timing, non-speech hallucination, readiness.

    `timed_provider`: the provider returns word timings on its other clips, so an empty output here is
    a measured zero over non-speech, not a missing measurement."""
    primary = metrics_for(language)[0]
    te = timing_error(ref.words, hyp_words, primary.tokenizer)
    duration_ms = None if pd.isna(duration_s) else duration_s * 1000
    # An empty output from a provider that does time its words invented nothing over non-speech.
    ns_words = [] if hyp_words is None and timed_provider and not hyp_text.strip() else hyp_words
    nh = nonspeech_hallucination(nonspeech_intervals(ref.nonspeech, ref.words, duration_ms), ns_words)
    rd = readiness(ref.text, hyp_text, language) if ref.punctuated else None
    return {
        "timing_n_words": te.n_words if te else None,
        "timing_median_ms": te.median_ms if te else None,
        "timing_p90_ms": te.p90_ms if te else None,
        "timing_within_200ms": te.within_200ms if te else None,
        "timing_n_within_200ms": sum(o <= 200 for o in te.offsets_ms) if te else None,
        "nonspeech_minutes": nh.nonspeech_minutes,
        "halluc_nonspeech_words": nh.words,
        "halluc_per_nonspeech_min": nh.per_minute,
        "punct_f1": rd.punct_f1 if rd else None,
        "casing_acc": rd.casing_acc if rd else None,
        "punct_tp": rd.punct_tp if rd else None,
        "punct_fp": rd.punct_fp if rd else None,
        "punct_fn": rd.punct_fn if rd else None,
        "casing_n": rd.casing_n if rd else None,
        "casing_correct": rd.casing_correct if rd else None,
        "category": ref.category,
        "audio_condition": audio_condition(ref.tags, ref.nonspeech, ref.reviewed),
        "conditions": json.dumps(clip_conditions(ref.conditions, ref.tags, ref.nonspeech)),
    }


def _score_rows(rows: pd.DataFrame, texts: pd.DataFrame, refs: _RefCache) -> pd.DataFrame:
    text_by_key = {(t.provider, t.video_id, int(t.repeat)): t for t in texts.itertuples(index=False)}
    main = rows[(rows["repeat"] == 0) & (rows["status"] == "ok")]
    timed = {t.provider for t in texts.itertuples(index=False) if _parse_words(t.words_json)}
    records: list[dict[str, Any]] = []
    for row in main.itertuples(index=False):
        ref = refs.get(row.video_id, row.tier)
        if ref is None:
            log.warning("no reference for %s (%s); row skipped", row.video_id, row.tier)
            continue
        hyp = text_by_key.get((row.provider, row.video_id, 0))
        if hyp is None:
            log.warning("no hypothesis text for %s / %s; scored as empty", row.provider, row.video_id)
        hyp_text = _text(hyp.hyp_text) if hyp is not None else ""
        hyp_words = _parse_words(hyp.words_json) if hyp is not None else None
        tags = ref.tags or _parse_tags(row.tags)
        duration = _num(row.duration_s)
        if pd.isna(duration):
            duration = _num(row.audio_seconds)
        v2 = _clip_v2(ref, hyp_text, hyp_words, row.language, duration, row.provider in timed)
        for spec in metrics_for(row.language):
            ref_tokens = refs.tokens(row.video_id, row.tier, spec)
            hyp_tokens = spec.tokenizer(hyp_text)
            a = align(ref_tokens, hyp_tokens)
            h = hallucination(len(ref_tokens), len(hyp_tokens), ref.words, hyp_words, spec.tokenizer)
            cs = codeswitch_error(ref.text, ref.codeswitch, hyp_tokens, spec)
            lat = refs.lattice(row.video_id, row.tier, spec, row.language)
            la = lattice_score(ref.text, ref.alternatives, hyp_tokens, spec, row.language, lattice=lat)
            records.append(
                {
                    "provider": row.provider,
                    "model_id": row.model_id,
                    "video_id": row.video_id,
                    "tier": row.tier,
                    "language": row.language,
                    "metric": spec.name,
                    "is_primary": spec.is_primary,
                    "ref_len": a.ref_len,
                    "hits": a.hits,
                    "subs": a.subs,
                    "dels": a.dels,
                    "ins": a.ins,
                    "errors": a.errors,
                    "error_rate": a.error_rate,
                    "hallucinated_tokens": h.hallucinated_tokens,
                    "hallucination_rate": h.hallucination_rate,
                    "length_ratio": h.length_ratio,
                    "long_output": h.long_output,
                    "latency_ms": _num(row.latency_ms),
                    "cost_usd": _num(row.cost_usd),
                    "audio_seconds": _num(row.audio_seconds),
                    "duration_s": duration,
                    "duration_bucket": duration_bucket(duration),
                    "tags": json.dumps(tags, ensure_ascii=False),
                    **v2,
                    "codeswitch_ref_len": cs.ref_len if cs else None,
                    "codeswitch_errors": cs.errors if cs else None,
                    "codeswitch_error_rate": cs.error_rate if cs else None,
                    "error_rate_lattice": la.error_rate,
                    "lattice_ref_len": la.ref_len,
                    "lattice_errors": la.errors,
                    "lattice_hits": la.hits,
                    "lattice_subs": la.subs,
                    "lattice_dels": la.dels,
                    "lattice_ins": la.ins,
                    "cached": _bool(getattr(row, "cached", False)),
                }
            )
    return pd.DataFrame(records, columns=SCORE_COLUMNS)


# ---------------------------------------------------------------------------
# Slices
# ---------------------------------------------------------------------------


def _slices(scores: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    """(slice_kind, slice, mask) for all, and each tag, duration bucket, category and audio condition present."""
    out: list[tuple[str, str, pd.Series]] = [("all", "all", pd.Series(True, index=scores.index))]
    tag_lists = scores["tags"].map(_parse_tags)
    for tag in TAGS:
        mask = tag_lists.map(lambda tags, t=tag: t in tags)
        if mask.any():
            out.append(("tag", tag, mask))
    for bucket in DURATION_BUCKETS:
        mask = scores["duration_bucket"] == bucket
        if mask.any():
            out.append(("duration", bucket, mask))
    for category in CATEGORIES:
        mask = scores["category"] == category
        if mask.any():
            out.append(("category", category, mask))
    for condition in ("music", "no_music"):
        mask = scores["audio_condition"] == condition
        if mask.any():
            out.append(("audio_condition", condition, mask))
    if "conditions" in scores.columns:
        cond_lists = scores["conditions"].map(_parse_tags)
        for condition in CONDITIONS:
            mask = cond_lists.map(lambda conds, c=condition: c in conds)
            if mask.any():
                out.append(("condition", condition, mask))
    return out


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def _model_id(group: pd.DataFrame) -> str:
    ids = sorted({str(m) for m in group["model_id"].dropna()})
    return ",".join(ids)


def _clusters(group: pd.DataFrame, channels: dict[str, str] | None) -> list[str] | None:
    """Channel per clip for the clustered bootstrap. A clip without a channel is its own cluster; a run
    without any channel information has no clustered interval (None)."""
    if not channels:
        return None
    ids = group["video_id"].astype(str)
    if not any(v in channels for v in ids):
        return None
    return [channels.get(v) or f"clip:{v}" for v in ids]


def _aggregate(group: pd.DataFrame, seed: int, channels: dict[str, str] | None = None) -> dict[str, Any]:
    ci = bootstrap_ci(group["errors"].to_numpy(), group["ref_len"].to_numpy(), seed=seed)
    clusters = _clusters(group, channels)
    if clusters is None:
        cl_low = cl_high = np.nan
        n_channels = None
    else:
        cl = cluster_bootstrap_ci(group["errors"].to_numpy(), group["ref_len"].to_numpy(), clusters, seed=seed)
        cl_low, cl_high, n_channels = cl.ci_low, cl.ci_high, cl.n
    ref_total = group["ref_len"].sum()
    audio_seconds = group["audio_seconds"].fillna(0).sum()
    audio_hours = audio_seconds / 3600
    cost_total = group["cost_usd"].fillna(0).sum()  # list cost, cached or not
    # Latency is measured on requests made in this run: a cache hit carries an earlier run's latency (taken
    # under that run's load, possibly with every provider uploading at once), and a latency of 0 means no
    # request was made (youtube-auto reads an existing track).
    timed = group["latency_ms"].notna() & (group["latency_ms"].astype(float) > 0)
    if "cached" in group:
        timed &= ~group["cached"].fillna(False).astype(bool)
    latency = group.loc[timed, "latency_ms"].astype(float)
    rtf_rows = group[timed & (group["audio_seconds"] > 0)]
    rtf = (rtf_rows["latency_ms"] / 1000 / rtf_rows["audio_seconds"]).mean() if len(rtf_rows) else np.nan
    return {
        "n_clips": int(len(group)),
        "error_rate": ci.rate,
        "ci_low": ci.ci_low,
        "ci_high": ci.ci_high,
        "too_small": bool(len(group) < TOO_SMALL),
        "hallucination_rate": float(group["hallucinated_tokens"].sum() / max(ref_total, 1)),
        "long_output_rate": float(group["long_output"].mean()),
        "cost_usd_total": float(cost_total),
        "audio_hours": float(audio_hours),
        "cost_per_hour": float(cost_total / audio_hours) if audio_hours > 0 else np.nan,
        "latency_p50": float(np.percentile(latency, 50)) if len(latency) else np.nan,
        "latency_p95": float(np.percentile(latency, 95)) if len(latency) else np.nan,
        "rtf": float(rtf),
        **_aggregate_v2(group),
        **_aggregate_lattice(group, seed),
        "ci_low_cluster": cl_low,
        "ci_high_cluster": cl_high,
        "n_channels": n_channels,
        "mean_clip_error_rate": mean_clip_rate(group["errors"].to_numpy(), group["ref_len"].to_numpy()),
    }


def _aggregate_lattice(group: pd.DataFrame, seed: int) -> dict[str, Any]:
    """Corpus lattice error rate (lattice errors over the closest paths' lengths) with a bootstrap CI."""
    if "lattice_errors" not in group or group["lattice_errors"].isna().all():
        return {"error_rate_lattice": np.nan, "lattice_ci_low": np.nan, "lattice_ci_high": np.nan}
    g = group[group["lattice_errors"].notna()]
    ci = bootstrap_ci(g["lattice_errors"].to_numpy(), g["lattice_ref_len"].to_numpy(), seed=seed)
    return {"error_rate_lattice": ci.rate, "lattice_ci_low": ci.ci_low, "lattice_ci_high": ci.ci_high}


def _ratio(num: float, den: float) -> float:
    return float(num / den) if den else np.nan


def _aggregate_v2(group: pd.DataFrame) -> dict[str, Any]:
    """Timing: medians across clips of the per clip median and p90, word weighted share within 200 ms.
    Everything else: corpus style (sum of counts over sum of counts)."""
    timed = group[group["timing_n_words"].fillna(0) > 0]
    words = timed["timing_n_words"].sum()
    ns = group[group["nonspeech_minutes"].notna() & group["halluc_nonspeech_words"].notna()]
    rd = group[group["punct_tp"].notna()]
    tp, fp, fn = rd["punct_tp"].sum(), rd["punct_fp"].sum(), rd["punct_fn"].sum()
    cs = group[group["codeswitch_ref_len"].fillna(0) > 0]
    return {
        "timing_n_clips": int(len(timed)),
        "timing_n_words": int(words),
        "timing_median_ms": float(timed["timing_median_ms"].median()) if len(timed) else np.nan,
        "timing_p90_ms": float(timed["timing_p90_ms"].median()) if len(timed) else np.nan,
        "timing_within_200ms": _ratio(timed["timing_n_within_200ms"].sum(), words),
        "nonspeech_n_clips": int(len(ns)),
        "nonspeech_minutes": float(ns["nonspeech_minutes"].sum()),
        "halluc_nonspeech_words": int(ns["halluc_nonspeech_words"].sum()),
        "halluc_per_nonspeech_min": _ratio(ns["halluc_nonspeech_words"].sum(), ns["nonspeech_minutes"].sum()),
        "readiness_n_clips": int(len(rd)),
        "punct_f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "casing_acc": _ratio(rd["casing_correct"].sum(), rd["casing_n"].sum()),
        "codeswitch_n_clips": int(len(cs)),
        "codeswitch_ref_len": int(cs["codeswitch_ref_len"].sum()),
        "codeswitch_errors": int(cs["codeswitch_errors"].sum()),
        "codeswitch_error_rate": _ratio(cs["codeswitch_errors"].sum(), cs["codeswitch_ref_len"].sum()),
    }


def _aggregates(scores: pd.DataFrame, seed: int, channels: dict[str, str] | None = None) -> pd.DataFrame:
    records = []
    for (tier, metric), part in scores.groupby(["tier", "metric"], sort=True):
        for kind, name, mask in _slices(part):
            sliced = part[mask]
            for provider, group in sliced.groupby("provider", sort=True):
                records.append(
                    {
                        "provider": provider,
                        "model_id": _model_id(group),
                        "tier": tier,
                        "metric": metric,
                        "slice_kind": kind,
                        "slice": name,
                        **_aggregate(group, seed, channels),
                    }
                )
    return pd.DataFrame(records, columns=AGGREGATE_COLUMNS)


# ---------------------------------------------------------------------------
# Paired comparisons
# ---------------------------------------------------------------------------


def _paired(scores: pd.DataFrame, seed: int, channels: dict[str, str] | None = None) -> pd.DataFrame:
    records = []
    for (tier, metric), part in scores.groupby(["tier", "metric"], sort=True):
        providers = sorted(part["provider"].unique())
        for kind, name, mask in _slices(part):
            sliced = part[mask]
            for a, b in combinations(providers, 2):
                sa = sliced[sliced["provider"] == a].set_index("video_id")
                sb = sliced[sliced["provider"] == b].set_index("video_id")
                common = sorted(set(sa.index) & set(sb.index))
                if not common:
                    continue
                res = paired_bootstrap(
                    sa.loc[common, "errors"].to_numpy(),
                    sb.loc[common, "errors"].to_numpy(),
                    sa.loc[common, "ref_len"].to_numpy(),
                    seed=seed,
                )
                shared = sa.loc[common].reset_index()
                clusters = _clusters(shared, channels)
                cl = None
                if clusters is not None:
                    cl = paired_cluster_bootstrap(
                        shared["errors"].to_numpy(),
                        sb.loc[common, "errors"].to_numpy(),
                        shared["ref_len"].to_numpy(),
                        clusters,
                        seed=seed,
                    )
                records.append(
                    {
                        "tier": tier,
                        "metric": metric,
                        "slice_kind": kind,
                        "slice": name,
                        "provider_a": a,
                        "provider_b": b,
                        "n_clips": res.n,
                        "diff": res.diff,
                        "ci_low": res.ci_low,
                        "ci_high": res.ci_high,
                        "p_value": res.p_value,
                        "verdict": res.verdict,
                        "ci_low_cluster": cl.ci_low if cl else np.nan,
                        "ci_high_cluster": cl.ci_high if cl else np.nan,
                        "p_value_cluster": cl.p_value if cl else np.nan,
                        "n_channels": cl.n if cl else None,
                    }
                )
    return pd.DataFrame(records, columns=PAIRED_COLUMNS)


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------


def pairwise_disagreement(a: list[str], b: list[str]) -> float:
    """Symmetric disagreement of two token lists: edit distance / max(len_a, len_b, 1)."""
    return align(a, b).errors / max(len(a), len(b), 1)


def _stability(rows: pd.DataFrame, texts: pd.DataFrame) -> pd.DataFrame:
    text_by_key = {(t.provider, t.video_id, int(t.repeat)): _text(t.hyp_text) for t in texts.itertuples(index=False)}
    reruns = rows[(rows["repeat"] >= 1) & (rows["status"] == "ok")]
    records = []
    for (provider, video_id), group in reruns.groupby(["provider", "video_id"], sort=True):
        spec = metrics_for(group["language"].iloc[0])[0]
        tokens = [spec.tokenizer(text_by_key.get((provider, video_id, int(r)), "")) for r in group["repeat"]]
        pairs = [pairwise_disagreement(x, y) for x, y in combinations(tokens, 2)]
        latency = group["latency_ms"].astype(float).dropna()
        records.append(
            {
                "provider": provider,
                "video_id": video_id,
                "n_repeats": int(len(group)),
                "text_disagreement": float(np.mean(pairs)) if pairs else np.nan,
                "latency_mean": float(latency.mean()) if len(latency) else np.nan,
                "latency_std": float(latency.std(ddof=1)) if len(latency) > 1 else np.nan,
            }
        )
    return pd.DataFrame(records, columns=STABILITY_COLUMNS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def clip_channels(rows: pd.DataFrame) -> dict[str, str]:
    """video_id -> channel from rows.parquet (empty when the run has no channel column)."""
    if "channel" not in rows.columns:
        return {}
    ch = rows[["video_id", "channel"]].dropna().drop_duplicates("video_id")
    return {str(r.video_id): str(r.channel) for r in ch.itertuples(index=False) if str(r.channel)}


def score_run(run_id: str, settings: Settings | None = None) -> Path:
    """Score a run and write the four parquet files. Returns results/<run_id>/."""
    s = settings or get_settings()
    rows = _read(s.runs_dir / run_id / "rows.parquet")
    texts = _read(s.runs_text_dir / run_id / "texts.parquet")
    refs = _RefCache(_read(s.runs_text_dir / run_id / "refs.parquet"))
    seed = s.sample_seed
    langs = sorted({str(x).split("-")[0].lower() for x in rows["language"].dropna()})
    if len(langs) > 1:
        # Aggregates are keyed by (tier, metric) and en, es and hi all score `wer`.
        raise ValueError(f"run {run_id} mixes languages ({', '.join(langs)}); a run must hold one language")

    scores = _score_rows(rows, texts, refs)
    channels = clip_channels(rows)
    aggregates = _aggregates(scores, seed, channels)
    paired = _paired(scores, seed, channels)
    stability = _stability(rows, texts)

    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(out / "scores.parquet", index=False)
    aggregates.to_parquet(out / "aggregates.parquet", index=False)
    paired.to_parquet(out / "paired.parquet", index=False)
    stability.to_parquet(out / "stability.parquet", index=False)
    return out
