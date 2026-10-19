"""Score a run: per clip metrics, aggregates with CIs, paired comparisons and stability.

Inputs (see docs/INTERFACES.md):
  results/runs/<run_id>/rows.parquet, data/runs/<run_id>/texts.parquet, data/runs/<run_id>/refs.parquet
Outputs in results/<run_id>/:
  scores.parquet, aggregates.parquet, paired.parquet, stability.parquet
"""

from __future__ import annotations

import json
import logging
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import TAGS, Word
from clipbench.scoring.metrics import align, hallucination
from clipbench.scoring.normalize import MetricSpec, metrics_for
from clipbench.scoring.stats import bootstrap_ci, paired_bootstrap

log = logging.getLogger(__name__)

TOO_SMALL = 15  # slices with fewer clips are flagged too_small
DURATION_BUCKETS = ["<15s", "15-30s", "30-60s", "60-90s"]

SCORE_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "metric", "is_primary", "ref_len", "hits",
    "subs", "dels", "ins", "errors", "error_rate", "hallucinated_tokens", "hallucination_rate",
    "length_ratio", "long_output", "latency_ms", "cost_usd", "audio_seconds", "duration_s",
    "duration_bucket", "tags",
]  # fmt: skip
AGGREGATE_COLUMNS = [
    "provider", "model_id", "tier", "metric", "slice_kind", "slice", "n_clips", "error_rate", "ci_low",
    "ci_high", "too_small", "hallucination_rate", "long_output_rate", "cost_usd_total", "audio_hours",
    "cost_per_hour", "latency_p50", "latency_p95", "rtf",
]  # fmt: skip
PAIRED_COLUMNS = [
    "tier", "metric", "slice_kind", "slice", "provider_a", "provider_b", "n_clips", "diff", "ci_low",
    "ci_high", "p_value", "verdict",
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


class _RefCache:
    """Tokenised references, so a slow normaliser (ja reading) runs once per clip and metric."""

    def __init__(self, refs: pd.DataFrame) -> None:
        self.refs = {(r.video_id, r.tier): r for r in refs.itertuples(index=False)}
        self._tokens: dict[tuple[str, str, str], list[str]] = {}

    def get(self, video_id: str, tier: str):
        return self.refs.get((video_id, tier))

    def tokens(self, video_id: str, tier: str, spec: MetricSpec) -> list[str]:
        key = (video_id, tier, spec.name)
        if key not in self._tokens:
            self._tokens[key] = spec.tokenizer(_text(self.refs[(video_id, tier)].ref_text))
        return self._tokens[key]


def _score_rows(rows: pd.DataFrame, texts: pd.DataFrame, refs: _RefCache) -> pd.DataFrame:
    text_by_key = {(t.provider, t.video_id, int(t.repeat)): t for t in texts.itertuples(index=False)}
    main = rows[(rows["repeat"] == 0) & (rows["status"] == "ok")]
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
        ref_words = _parse_words(ref.words_json)
        tags = _parse_tags(getattr(ref, "tags", None)) or _parse_tags(row.tags)
        duration = _num(row.duration_s)
        if pd.isna(duration):
            duration = _num(row.audio_seconds)
        for spec in metrics_for(row.language):
            ref_tokens = refs.tokens(row.video_id, row.tier, spec)
            hyp_tokens = spec.tokenizer(hyp_text)
            a = align(ref_tokens, hyp_tokens)
            h = hallucination(len(ref_tokens), len(hyp_tokens), ref_words, hyp_words, spec.tokenizer)
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
                }
            )
    return pd.DataFrame(records, columns=SCORE_COLUMNS)


# ---------------------------------------------------------------------------
# Slices
# ---------------------------------------------------------------------------


def _slices(scores: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    """(slice_kind, slice, boolean mask) for all, each tag present, each duration bucket present."""
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
    return out


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def _model_id(group: pd.DataFrame) -> str:
    ids = sorted({str(m) for m in group["model_id"].dropna()})
    return ",".join(ids)


def _aggregate(group: pd.DataFrame, seed: int) -> dict[str, Any]:
    ci = bootstrap_ci(group["errors"].to_numpy(), group["ref_len"].to_numpy(), seed=seed)
    ref_total = group["ref_len"].sum()
    audio_seconds = group["audio_seconds"].fillna(0).sum()
    audio_hours = audio_seconds / 3600
    cost_total = group["cost_usd"].fillna(0).sum()  # list cost, cached or not
    latency = group["latency_ms"].dropna()
    rtf_rows = group[(group["audio_seconds"] > 0) & group["latency_ms"].notna()]
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
    }


def _aggregates(scores: pd.DataFrame, seed: int) -> pd.DataFrame:
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
                        **_aggregate(group, seed),
                    }
                )
    return pd.DataFrame(records, columns=AGGREGATE_COLUMNS)


# ---------------------------------------------------------------------------
# Paired comparisons
# ---------------------------------------------------------------------------


def _paired(scores: pd.DataFrame, seed: int) -> pd.DataFrame:
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


def score_run(run_id: str, settings: Settings | None = None) -> Path:
    """Score a run and write the four parquet files. Returns results/<run_id>/."""
    s = settings or get_settings()
    rows = _read(s.runs_dir / run_id / "rows.parquet")
    texts = _read(s.runs_text_dir / run_id / "texts.parquet")
    refs = _RefCache(_read(s.runs_text_dir / run_id / "refs.parquet"))
    seed = s.sample_seed

    scores = _score_rows(rows, texts, refs)
    aggregates = _aggregates(scores, seed)
    paired = _paired(scores, seed)
    stability = _stability(rows, texts)

    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(out / "scores.parquet", index=False)
    aggregates.to_parquet(out / "aggregates.parquet", index=False)
    paired.to_parquet(out / "paired.parquet", index=False)
    stability.to_parquet(out / "stability.parquet", index=False)
    return out
