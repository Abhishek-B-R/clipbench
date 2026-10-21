"""Publication rules: the one place that decides whether a language gets a ranking, which providers may
appear in it, and in what order.

METHODOLOGY.md, "When a language is published": a language is published only when its latest run has
- at least MIN_VERIFIED_CLIPS verified clips,
- at least MIN_PROVIDERS_OK ELIGIBLE providers: a provider is eligible when it has an ok repeat-0 row on at
  least `min_provider_coverage` (clipbench.toml, default 0.9) of the language's verified evaluation clips,
- and the verified clips pass the diversity rules (clipbench.data.check.check_verified).
Below that every output (report.md, site/, the web export, OG images) shows the method and progress, never
a ranking, and no findings. Excluded providers never appear in a ranking, a finding or a "beats" sentence,
published or not. The export carries every field the site and Worker need (docs/V3.md, "Publication gate
fields"); the web app mirrors only the thresholds in web/src/lib/config.ts.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from clipbench.config import Settings

LANGUAGES = ("en", "ja", "ko", "es", "hi")
MIN_VERIFIED_CLIPS = 100
MIN_PROVIDERS_OK = 6
DEFAULT_MIN_PROVIDER_COVERAGE = 0.9
# Same rule as clipbench.scoring.score.TOO_SMALL: a slice with fewer clips supports no claim.
MIN_SLICE_CLIPS = 15


def min_provider_coverage(settings: Settings) -> float:
    """`min_provider_coverage` from clipbench.toml (env CLIPBENCH_MIN_PROVIDER_COVERAGE overrides), a share
    in [0, 1]. Read here rather than in clipbench.config so the gate has a single source of truth."""
    raw: Any = os.environ.get("CLIPBENCH_MIN_PROVIDER_COVERAGE")
    if raw is None:
        path = settings.root / "clipbench.toml"
        try:
            raw = tomllib.loads(path.read_text()).get("min_provider_coverage")
        except (OSError, tomllib.TOMLDecodeError):
            raw = None
    if raw is None:
        return DEFAULT_MIN_PROVIDER_COVERAGE
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"min_provider_coverage must be between 0 and 1, got {value}")
    return value


def _empty_diversity() -> dict[str, Any]:
    return {
        "channels": 0,
        "largest_channel_share": None,
        "categories": [],
        "passes": False,
        "problems": ["no verified clips"],
    }


def _empty_gate() -> dict[str, bool]:
    return {"verified_clips_ok": False, "providers_ok": False, "diversity_ok": False}


@dataclass
class ProviderCoverage:
    provider: str
    coverage: float | None  # share of the verified evaluation clips with an ok repeat-0 row; None = no clips
    n_scored: int
    n_skipped: int | None  # None when the run has no rows.parquet to count from
    n_error: int | None
    n_missing: int | None  # evaluation clips with no row at all for this provider
    n_eval: int
    eligible: bool = False
    reason: str | None = None

    def excluded_entry(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "coverage": None if self.coverage is None else round(self.coverage, 4),
            "n_scored": self.n_scored,
            "n_skipped": self.n_skipped,
            "n_error": self.n_error,
            "reason": self.reason,
        }


@dataclass
class LanguageStatus:
    lang: str
    verified_clips: int = 0
    # Number of ELIGIBLE providers (len(providers_eligible)). Kept under its v2 name so older readers of
    # the export still gate on the right number.
    providers_ok: int = 0
    published: bool = False
    # Legacy descriptive fields: every scored verified clip (categories as scored, guesses included).
    channels: int = 0
    categories: list[str] = field(default_factory=list)
    largest_channel_share: float | None = None
    # Publication gate fields (docs/V3.md, "Publication gate fields").
    providers_eligible: list[str] = field(default_factory=list)
    providers_excluded: list[dict[str, Any]] = field(default_factory=list)
    min_coverage: float = DEFAULT_MIN_PROVIDER_COVERAGE
    verified_diversity: dict[str, Any] = field(default_factory=_empty_diversity)
    gate: dict[str, bool] = field(default_factory=_empty_gate)
    # Not exported: per provider coverage for the report's funnel.
    provider_coverage: dict[str, ProviderCoverage] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("provider_coverage", None)
        return out

    @property
    def eligible(self) -> set[str]:
        return set(self.providers_eligible)


def is_published(verified_clips: int, providers_eligible: int, diversity_ok: bool) -> bool:
    return (
        verified_clips >= MIN_VERIFIED_CLIPS and providers_eligible >= MIN_PROVIDERS_OK and bool(diversity_ok)
    )


def _rows(settings: Settings, run_id: str) -> pd.DataFrame | None:
    path = settings.runs_dir / run_id / "rows.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - unreadable rows count as missing
        return None


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][^\s/\\:'\"]+){2,}[/\\]?")


def no_paths(reason: str) -> str:
    """Skip and error reasons can name local files; the published line keeps only the reason."""
    return _PATH_RE.sub("<path>", reason)


# Why a provider has no result on a clip, in words a reader cannot mistake for a model failure.
_SKIP_WORDS = {"quota": "account quota", "budget": "budget cap reached"}


def gap_text(grp: pd.DataFrame) -> str:
    """'not run on 122 (account quota), failed on 2 (TimeoutError)' for one provider's repeat-0 rows that
    are not ok, counted per distinct clip (a clip with an ok row is never a gap). Quota and budget stops are
    "not run": the request never went out, so they say nothing about the model."""
    if grp.empty or "status" not in grp:
        return ""
    ok = set(grp.loc[grp["status"] == "ok", "video_id"].astype(str))
    bad = grp[(grp["status"] != "ok") & ~grp["video_id"].astype(str).isin(ok)].drop_duplicates("video_id")
    reasons = bad["error"] if "error" in bad else pd.Series([None] * len(bad), index=bad.index)
    counts: Counter[tuple[str, str]] = Counter()
    for status, err in zip(bad["status"].astype(str), reasons, strict=True):
        text = "" if err is None or (isinstance(err, float) and pd.isna(err)) or err is pd.NA else str(err)
        if status == "skipped" and text in _SKIP_WORDS:
            counts[("not run on", _SKIP_WORDS[text])] += 1
        elif status == "skipped":
            counts[("skipped on", no_paths(text)[:60] or "skipped")] += 1
        else:
            label = no_paths(text).split(":")[0][:60] if text else status
            counts[("failed on", label)] += 1
    return ", ".join(f"{verb} {n} ({why})" for (verb, why), n in counts.most_common())


def provider_coverage(
    rows: pd.DataFrame | None,
    eval_ids: set[str],
    aggregates: pd.DataFrame,
    min_coverage: float,
) -> dict[str, ProviderCoverage]:
    """Coverage of every provider seen in the run on the verified evaluation clips.

    With rows.parquet: n_scored, n_skipped and n_error count distinct evaluation clips with a repeat-0 row
    of that status (ok wins over the others). Without it: n_scored is the provider's verified 'all'
    aggregate n_clips and the other counts are unknown (None)."""
    n_eval = len(eval_ids)
    out: dict[str, ProviderCoverage] = {}
    if rows is not None and {"provider", "status", "video_id"} <= set(rows.columns):
        main = rows[rows["repeat"] == 0] if "repeat" in rows else rows
        for pid, grp in main.groupby("provider", sort=True):
            vids = grp["video_id"].astype(str)
            on_eval = grp[vids.isin(eval_ids)]
            ok = set(on_eval.loc[on_eval["status"] == "ok", "video_id"].astype(str))
            skipped = set(on_eval.loc[on_eval["status"] == "skipped", "video_id"].astype(str)) - ok
            error = set(on_eval.loc[on_eval["status"] == "error", "video_id"].astype(str)) - ok - skipped
            seen = set(on_eval["video_id"].astype(str))
            out[str(pid)] = ProviderCoverage(
                provider=str(pid),
                coverage=len(ok) / n_eval if n_eval else None,
                n_scored=len(ok),
                n_skipped=len(skipped),
                n_error=len(error),
                n_missing=n_eval - len(seen),
                n_eval=n_eval,
            )
    elif not aggregates.empty:
        ver = aggregates[(aggregates["tier"] == "verified") & (aggregates["slice_kind"] == "all")]
        for pid, grp in ver.groupby("provider", sort=True):
            n = int(grp["n_clips"].max()) if len(grp) else 0
            out[str(pid)] = ProviderCoverage(
                provider=str(pid),
                coverage=min(1.0, n / n_eval) if n_eval else None,
                n_scored=n,
                n_skipped=None,
                n_error=None,
                n_missing=None,
                n_eval=n_eval,
            )
    # Providers with numbers in the aggregates but no rows at all still get an entry.
    if not aggregates.empty:
        for pid in sorted(set(aggregates["provider"].astype(str)) - set(out)):
            out[pid] = ProviderCoverage(pid, 0.0 if n_eval else None, 0, None, None, None, n_eval)

    gaps: dict[str, str] = {}
    if rows is not None and {"provider", "status", "video_id"} <= set(rows.columns):
        main = rows[rows["repeat"] == 0] if "repeat" in rows else rows
        gaps = {str(pid): gap_text(grp) for pid, grp in main.groupby("provider", sort=True)}
    for pc in out.values():
        if pc.coverage is None:
            gap = gaps.get(pc.provider)
            pc.reason = "no verified clips in this run" + (f"; on the clips it was given: {gap}" if gap else "")
        elif pc.coverage + 1e-12 >= min_coverage:
            pc.eligible = True
        else:
            counts = f"{pc.n_skipped} skipped, {pc.n_error} errored" if pc.n_skipped is not None else ""
            missing = f", {pc.n_missing} with no result" if pc.n_missing else ""
            pc.reason = (
                f"coverage {_pct(pc.coverage)} ({pc.n_scored} of {pc.n_eval} verified clips scored"
                + (f"; {counts}{missing}" if counts else "")
                + f") is below the {_pct(min_coverage)} minimum"
            )
    return out


def verified_diversity(eval_ids: set[str], channels: dict[str, str], settings: Settings) -> dict[str, Any]:
    """Diversity of the verified evaluation clips (clipbench.data.check.check_verified): channels from the
    run's rows, categories from the verified files only."""
    from clipbench.data.check import check_verified, diversity_contract

    if not eval_ids:
        return _empty_diversity()
    clips = pd.DataFrame(
        {"video_id": sorted(eval_ids), "channel": [channels.get(v) for v in sorted(eval_ids)]}
    )
    return diversity_contract(check_verified(clips, settings.verified_dir))


def run_status(
    run_id: str, language: str, aggregates: pd.DataFrame, scores: pd.DataFrame | None, settings: Settings
) -> LanguageStatus:
    """Publication status of one run's language.

    verified_clips: distinct verified clips scored (scores.parquet, else the verified 'all' aggregate).
    The verified evaluation set is every verified clip in the run: verified repeat-0 rows of rows.parquet
    plus the verified clips in scores.parquet. Coverage, eligibility and diversity are computed on it.
    channels, categories and the largest channel share are the legacy descriptive fields (verified clips
    when there are any, else every clip in the run).
    """
    st = LanguageStatus(lang=language, min_coverage=min_provider_coverage(settings))
    verified_ids: set[str] = set()
    all_ids: set[str] = set()
    if scores is not None and not scores.empty:
        all_ids = set(scores["video_id"].astype(str))
        verified_ids = set(scores.loc[scores["tier"] == "verified", "video_id"].astype(str))
    if not verified_ids and not aggregates.empty:
        ver = aggregates[(aggregates["tier"] == "verified") & (aggregates["slice_kind"] == "all")]
        st.verified_clips = int(ver["n_clips"].max()) if len(ver) else 0
    else:
        st.verified_clips = len(verified_ids)

    rows = _rows(settings, run_id)
    eval_ids = set(verified_ids)
    if rows is not None and {"video_id", "tier"} <= set(rows.columns):
        main = rows[rows["repeat"] == 0] if "repeat" in rows else rows
        eval_ids |= set(main.loc[main["tier"] == "verified", "video_id"].astype(str))
    if not eval_ids and st.verified_clips:
        # No per clip ids (aggregates only): coverage is relative to the verified clip count.
        eval_ids = {f"<clip {i}>" for i in range(st.verified_clips)}
        rows_for_cov = None
    else:
        rows_for_cov = rows

    cov = provider_coverage(rows_for_cov, eval_ids, aggregates, st.min_coverage)
    st.provider_coverage = cov
    st.providers_eligible = sorted(p for p, c in cov.items() if c.eligible)
    st.providers_excluded = [c.excluded_entry() for p, c in sorted(cov.items()) if not c.eligible]
    st.providers_ok = len(st.providers_eligible)

    channel_of: dict[str, str] = {}
    if rows is not None and {"video_id", "channel"} <= set(rows.columns):
        ch = rows.drop_duplicates("video_id")
        channel_of = {
            str(r.video_id): str(r.channel) for r in ch.itertuples(index=False) if pd.notna(r.channel)
        }
    real_eval = {v for v in eval_ids if not v.startswith("<clip ")}
    st.verified_diversity = verified_diversity(real_eval, channel_of, settings)

    ids = verified_ids or all_ids
    if channel_of:
        counts = Counter(c for v, c in channel_of.items() if not ids or v in ids)
        st.channels = len(counts)
        total = sum(counts.values())
        st.largest_channel_share = round(max(counts.values()) / total, 4) if total else None
    if scores is not None and "category" in scores:
        sub = scores[scores["video_id"].astype(str).isin(ids)] if ids else scores
        st.categories = sorted({str(c) for c in sub["category"].dropna() if str(c)})

    st.gate = {
        "verified_clips_ok": st.verified_clips >= MIN_VERIFIED_CLIPS,
        "providers_ok": st.providers_ok >= MIN_PROVIDERS_OK,
        "diversity_ok": bool(st.verified_diversity.get("passes")),
    }
    st.published = is_published(st.verified_clips, st.providers_ok, st.gate["diversity_ok"])
    return st


def all_languages(statuses: dict[str, LanguageStatus]) -> list[dict[str, Any]]:
    """One entry per v2 language, zeros for languages without a run, plus any other language seen."""
    langs = list(LANGUAGES) + sorted(set(statuses) - set(LANGUAGES))
    return [(statuses.get(lang) or LanguageStatus(lang=lang)).as_dict() for lang in langs]


# ---------------------------------------------------------------- eligible providers and the ranking


def eligible_aggregates(aggregates: pd.DataFrame, eligible: set[str]) -> pd.DataFrame:
    return aggregates[aggregates["provider"].astype(str).isin(eligible)]


def eligible_paired(paired: pd.DataFrame | None, eligible: set[str]) -> pd.DataFrame | None:
    """Paired tests between two eligible providers only. Exclusion depends on coverage, not on results,
    so dropping these tests before the Benjamini-Hochberg step keeps the correction valid."""
    if paired is None or paired.empty:
        return paired
    keep = paired["provider_a"].astype(str).isin(eligible) & paired["provider_b"].astype(str).isin(eligible)
    return paired[keep]


def rank_order(rows: pd.DataFrame) -> pd.DataFrame:
    """The ranking order, defined once: primary error rate ascending, ties broken by provider id. Rows
    without an error rate are dropped."""
    out = rows.dropna(subset=["error_rate"]).copy()
    out["_pid"] = out["provider"].astype(str)
    return out.sort_values(["error_rate", "_pid"], kind="stable").drop(columns="_pid")


def ranking(aggregates: pd.DataFrame, eligible: set[str], tier: str, metric: str) -> list[dict[str, Any]]:
    """[{provider, rank, error_rate, ci_low, ci_high, n_clips}] for slice all, eligible providers only."""
    a = aggregates
    rows = a[(a["tier"] == tier) & (a["metric"] == metric) & (a["slice_kind"] == "all")]
    rows = rank_order(eligible_aggregates(rows, eligible))
    return [
        {
            "provider": str(r.provider),
            "rank": i,
            "error_rate": float(r.error_rate),
            "ci_low": float(r.ci_low),
            "ci_high": float(r.ci_high),
            "n_clips": int(r.n_clips),
        }
        for i, r in enumerate(rows.itertuples(index=False), start=1)
    ]


def ranking_section(aggregates: pd.DataFrame, status: LanguageStatus | None) -> dict[str, dict[str, list]]:
    """{tier: {metric: ranking}} for a published language; {} otherwise (no ranking below the gate)."""
    if status is None or not status.published:
        return {}
    out: dict[str, dict[str, list]] = {}
    for tier in sorted(set(aggregates["tier"].astype(str))):
        for metric in sorted(set(aggregates.loc[aggregates["tier"] == tier, "metric"].astype(str))):
            out.setdefault(tier, {})[metric] = ranking(aggregates, status.eligible, tier, metric)
    return out


def annotate_aggregates(aggregates: pd.DataFrame, status: LanguageStatus | None) -> pd.DataFrame:
    """Every aggregates row gains `eligible` (bool) and `coverage` (the provider's share of the verified
    evaluation clips with an ok repeat-0 row; null when the run has no verified clips)."""
    out = aggregates.copy()
    cov = status.provider_coverage if status else {}
    pids = out["provider"].astype(str)
    out["eligible"] = pids.map(lambda p: bool(cov.get(p) and cov[p].eligible))
    out["coverage"] = pids.map(lambda p: None if cov.get(p) is None or cov[p].coverage is None
                               else round(cov[p].coverage, 4))  # fmt: skip
    return out
