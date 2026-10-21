"""Build the report for one run: results/<run_id>/report.md and site/index.html.

The report is assembled as a list of blocks (heading, paragraph, table, chart, list) and rendered
twice: to Markdown here and to HTML by clipbench.report.site. Tiers are never mixed: the verified
tier is the headline; the creator tier gets its own section labelled "unverified reference".
"""

from __future__ import annotations

import json
import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.report import charts, findings, rules

TIER_TITLES = {"verified": "Verified references", "creator": "Creator captions (unverified reference)"}
FALLBACK_PRIMARY = {"ja": "cer", "es": "wer", "ko": "cer_nospace", "en": "wer", "hi": "wer"}
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "es": "Spanish", "ko": "Korean", "hi": "Hindi"}
CAPTION_METRICS_DOC = "METHODOLOGY.md, section Caption metrics"
METHODOLOGY_DOC = "METHODOLOGY.md"


# ---------------------------------------------------------------- blocks


@dataclass
class Block:
    kind: str  # h2 h3 p table chart list
    text: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    name: str = ""
    spec: dict | None = None
    caption: str = ""
    css: str = ""  # optional class hint for the site


@dataclass
class RunData:
    run_id: str
    language: str
    metric: str
    aggregates: pd.DataFrame
    paired: pd.DataFrame | None
    stability: pd.DataFrame | None
    scores: pd.DataFrame | None
    errors: dict | None
    meta: dict
    status: rules.LanguageStatus | None = None
    # provider -> one line on clips it did not score (repeat 0), from rows.parquet; empty when all scored
    coverage: dict[str, str] = field(default_factory=dict)
    # Every provider's rows, before the eligibility filter (the export annotates and publishes them all).
    all_aggregates: pd.DataFrame | None = None
    all_paired: pd.DataFrame | None = None
    # Dataset funnel from the sample to the verified set (clipbench.data.check.funnel); None without data/.
    funnel: dict[str, int] | None = None
    # provider -> (ok clips, clips in the run) on repeat 0, all tiers; for the funnel while nothing is verified
    run_counts: dict[str, tuple[int, int]] = field(default_factory=dict)
    # (guessed, total) categories of the non-verified clips (guessed_categories)
    category_guesses: tuple[int, int] = (0, 0)

    @property
    def published(self) -> bool:
        return bool(self.status and self.status.published)

    @property
    def eligible(self) -> set[str]:
        return self.status.eligible if self.status else set()


def apply_gate(data: RunData) -> RunData:
    """What the report and site show (rules.py is the source of truth):
    - paired: tests between eligible providers only, Benjamini-Hochberg adjusted over that family, always;
    - aggregates and stability: eligible providers only when the language is published; every provider
      (in provider order, ranking nothing) while it is a progress check."""
    data.all_aggregates = data.aggregates if data.all_aggregates is None else data.all_aggregates
    data.all_paired = data.paired if data.all_paired is None else data.all_paired
    eligible = data.eligible
    data.paired = findings.adjust_paired(rules.eligible_paired(data.all_paired, eligible))
    if data.published:
        data.aggregates = rules.eligible_aggregates(data.all_aggregates, eligible)
        if data.stability is not None and not data.stability.empty and "provider" in data.stability:
            data.stability = data.stability[data.stability["provider"].astype(str).isin(eligible)]
    return data


def _read(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def _primary_metric(language: str, scores: pd.DataFrame | None, aggregates: pd.DataFrame) -> str:
    if scores is not None and "is_primary" in scores:
        prim = scores[scores["is_primary"].astype(bool)]["metric"].unique()
        if len(prim) == 1:
            return str(prim[0])
    try:
        from clipbench.scoring.normalize import metrics_for

        return next(s.name for s in metrics_for(language) if s.is_primary)
    except Exception:  # noqa: BLE001 - scoring optional at import time
        pass
    if language in FALLBACK_PRIMARY:
        return FALLBACK_PRIMARY[language]
    return str(aggregates["metric"].iloc[0])


_no_paths = rules.no_paths


def coverage_notes(rows: pd.DataFrame | None) -> dict[str, str]:
    """'<provider> scored 28 of 150 clips, not run on 122 (account quota)' for providers that missed
    clips. Quota and budget stops are "not run", never model failures (rules.gap_text)."""
    if rows is None or not {"provider", "status", "video_id"} <= set(rows.columns):
        return {}
    main = rows[rows["repeat"] == 0] if "repeat" in rows else rows
    out: dict[str, str] = {}
    for pid, grp in main.groupby("provider", sort=True):
        total = int(grp["video_id"].nunique())
        ok = int(grp.loc[grp["status"] == "ok", "video_id"].nunique())
        if ok == total:
            continue
        gaps = rules.gap_text(grp) or "not scored"
        out[str(pid)] = f"{pid} scored {ok} of {total} clips, {gaps}"
    return out


def latency_mode(meta: dict) -> dict[str, Any]:
    """How latency was measured in this run, from run_meta.latency_mode. Runs recorded before that field
    existed ran every provider at the same time, so their latency is contended."""
    lm = meta.get("latency_mode")
    if isinstance(lm, dict) and lm.get("sequential_providers") is not None:
        seq = bool(lm.get("sequential_providers"))
        conc = lm.get("max_concurrency")
        if seq:
            text = "Latency was measured one provider at a time" + (
                f" (up to {int(conc)} requests in flight for that provider)" if conc else ""
            )
        else:
            text = "Latency was measured with every provider running at the same time, so it is contended"
        short = (f"one provider at a time, up to {int(conc)} requests in flight" if conc else "one provider at a time") \
            if seq else "contended (providers ran at the same time)"
        return {"sequential_providers": seq, "max_concurrency": conc, "contended": not seq, "text": text + ".",
                "short": short}  # fmt: skip
    return {
        "sequential_providers": None,
        "max_concurrency": None,
        "contended": True,
        "text": "This run predates sequential latency measurement: providers ran at the same time, so latency "
        "is contended and only a rough guide.",
        "short": "contended (recorded before providers ran one at a time)",
    }


def _clip_sources(scores: pd.DataFrame | None) -> str:
    """'YouTube Shorts', or 'YouTube Shorts and short windows cut from longer YouTube videos'."""
    if scores is None or "video_id" not in scores or scores.empty:
        return "YouTube Shorts"
    windows = scores["video_id"].astype(str).str.contains("@").any()
    shorts = (~scores["video_id"].astype(str).str.contains("@")).any()
    if windows and shorts:
        return "YouTube Shorts and short windows cut from longer YouTube videos"
    if windows:
        return "short windows cut from longer YouTube videos"
    return "YouTube Shorts"


def guessed_categories(settings: Settings, language: str, scores: pd.DataFrame | None) -> tuple[int, int]:
    """(clips whose category is a manifest guess, clips with a category) among the run's non-verified
    clips, from the sample's category_guess column. (0, 0) when unknown."""
    if scores is None or scores.empty or "category" not in scores:
        return (0, 0)
    sub = scores[scores["tier"] != "verified"].drop_duplicates("video_id")
    with_cat = sub[sub["category"].notna()]
    if with_cat.empty:
        return (0, 0)
    try:
        from clipbench.data.sample import load_sample

        sample = load_sample(settings, language)
    except Exception:  # noqa: BLE001 - no sample: say nothing rather than guess
        return (0, len(with_cat))
    if "category_guess" not in sample:
        return (0, len(with_cat))
    key = "clip_id" if "clip_id" in sample else "video_id"
    guess = dict(zip(sample[key].astype(str), sample["category_guess"].fillna(False).astype(bool), strict=False))
    n = sum(bool(guess.get(str(v), False)) for v in with_cat["video_id"])
    return (n, len(with_cat))


def load_run(run_id: str, settings: Settings | None = None) -> RunData:
    settings = settings or get_settings()
    res = settings.results_dir / run_id
    aggregates = _read(res / "aggregates.parquet")
    if aggregates is None:
        raise FileNotFoundError(
            f"{res / 'aggregates.parquet'} not found; run `clipbench score {run_id}` first"
        )
    meta_path = settings.runs_dir / run_id / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    errors_path = res / "errors.json"
    errors = json.loads(errors_path.read_text()) if errors_path.exists() else None
    scores = _read(res / "scores.parquet")
    rows = rules._rows(settings, run_id)
    language = str(
        meta.get("language")
        or (scores["language"].iloc[0] if scores is not None and len(scores) else "")
        or settings.language
    ).split("-")[0]
    data = RunData(
        run_id=run_id,
        language=language,
        status=rules.run_status(run_id, language, aggregates, scores, settings),
        metric=_primary_metric(language, scores, aggregates),
        aggregates=aggregates,
        paired=_read(res / "paired.parquet"),
        stability=_read(res / "stability.parquet"),
        scores=scores,
        errors=errors,
        meta=meta,
        coverage=coverage_notes(rows),
        funnel=dataset_funnel(settings, language),
        run_counts=run_counts(rows),
        category_guesses=guessed_categories(settings, language, scores),
    )
    return apply_gate(data)


def run_counts(rows: pd.DataFrame | None) -> dict[str, tuple[int, int]]:
    if rows is None or not {"provider", "status", "video_id"} <= set(rows.columns):
        return {}
    main = rows[rows["repeat"] == 0] if "repeat" in rows else rows
    return {
        str(pid): (int(g.loc[g["status"] == "ok", "video_id"].nunique()), int(g["video_id"].nunique()))
        for pid, g in main.groupby("provider", sort=True)
    }


def dataset_funnel(settings: Settings, language: str) -> dict[str, int] | None:
    """Sampled, audio available, reviewed, skipped, unusable and verified clips of the language's sample.
    Reads only file presence and verified-file metadata; None when there is no sample."""
    from clipbench.data.check import funnel
    from clipbench.data.info import read_json
    from clipbench.data.sample import load_sample

    try:
        sample = load_sample(settings, language)
    except (FileNotFoundError, OSError, ValueError):
        return None
    hashes = read_json(settings.audio_dir / "hashes.json", {})
    return funnel(
        sample, settings.audio_dir, settings.verified_dir, hashes if isinstance(hashes, dict) else {}
    )


# ---------------------------------------------------------------- formatting


def _nan(x: Any) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or (x is pd.NA)


def pct(x: Any) -> str:
    return "n/a" if _nan(x) else f"{float(x) * 100:.1f}%"


def money(x: Any) -> str:
    return "n/a" if _nan(x) else f"${float(x):.2f}"


def seconds(ms: Any) -> str:
    return "n/a" if _nan(ms) else f"{float(ms) / 1000:.2f} s"


def _pct_short(x: Any) -> str:
    """50% rather than 50.0% for round thresholds."""
    if _nan(x):
        return "n/a"
    v = float(x) * 100
    return f"{v:.0f}%" if abs(v - round(v)) < 1e-9 else f"{v:.1f}%"


def num(x: Any, digits: int = 3) -> str:
    return "n/a" if _nan(x) else f"{float(x):.{digits}f}"


# ---------------------------------------------------------------- meta helpers


def provider_meta(meta: dict) -> list[dict]:
    """Normalise run_meta providers into [{id, model_id, checked_on, usd_per_minute}]."""
    raw = meta.get("providers") or []
    if isinstance(raw, dict):
        raw = [{"id": k, **(v if isinstance(v, dict) else {"model_id": v})} for k, v in raw.items()]
    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "id": p.get("id") or p.get("provider"),
                "model_id": p.get("model_id"),
                "checked_on": p.get("pricing_checked_on")
                or p.get("checked_on")
                or (p.get("pricing") or {}).get("checked_on"),
                "usd_per_minute": p.get("usd_per_minute"),
            }
        )
    return out


def _pricing_toml() -> dict:
    path = Path(__file__).resolve().parent.parent / "providers" / "pricing.toml"
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def methodology_url() -> str | None:
    return os.environ.get("CLIPBENCH_METHODOLOGY_URL") or None


# ---------------------------------------------------------------- sections


def headline_table(
    agg: pd.DataFrame, tier: str, metric: str, ranked: bool = True, latency_note: str = ""
) -> Block:
    rows = agg[(agg["tier"] == tier) & (agg["metric"] == metric) & (agg["slice_kind"] == "all")]
    # Unpublished languages get no ranking: rows in provider order, not by error rate. A published
    # language's rows hold eligible providers only, in the one ranking order of rules.rank_order.
    rows = rules.rank_order(rows) if ranked else rows.sort_values("provider")
    body = [
        [
            str(r["provider"]),
            str(r["model_id"]),
            f"{pct(r['error_rate'])} ({pct(r['ci_low'])} to {pct(r['ci_high'])})",
            money(r["cost_per_hour"]),
            seconds(r["latency_p50"]),
            num(r["rtf"]),
            str(int(r["n_clips"])),
        ]
        for _, r in rows.iterrows()
    ]
    label = "" if tier == "verified" else " Unverified reference: creator captions may not be verbatim."
    return Block(
        "table",
        headers=[
            "provider",
            "model",
            f"{metric} (95% CI)",
            "cost / audio hour",
            "p50 latency",
            "RTF",
            "n clips",
        ],
        rows=body,
        caption=f"Tier: {tier}. Metric: {metric}, lower is better. Slice: all clips.{label}"
        + (
            ""
            if ranked
            else " Not a ranking: this language is not published yet, rows are in provider order."
        )
        + " Latency and RTF cover requests made in this run (cache hits and systems that make no request, such "
        "as youtube-auto, are n/a)."
        + (f" {latency_note}" if latency_note else "")
        + " "
        f"(source: aggregates.parquet, tier={tier}, metric={metric}, slice=all)",
        css="headline" if tier == "verified" else "unverified",
    )


def chart_blocks(data: RunData, tier: str, out_dirs: list[Path]) -> list[Block]:
    agg = data.aggregates[(data.aggregates["tier"] == tier) & (data.aggregates["metric"] == data.metric)]
    overall = agg[agg["slice_kind"] == "all"]
    if overall.empty:
        return []
    suffix = "" if tier == "verified" else " (unverified reference)"
    builders: list[tuple[str, Any, str]] = [
        (
            "accuracy_vs_cost",
            lambda: charts.accuracy_vs_cost(overall, data.metric, f"Accuracy vs cost{suffix}"),
            "Error rate with 95% CI against cost per audio hour (log axis). Zero cost providers sit at the left floor "
            "with their own marker.",
        ),
    ]
    if (agg["slice_kind"] == "tag").any():
        builders.append(
            (
                "by_tag",
                lambda: charts.by_slice(agg, data.metric, f"Error rate by tag{suffix}"),
                f"Grey bars are slices {charts.TOO_SMALL_LABEL}.",
            )
        )
    builders.append(
        (
            "hallucination",
            lambda: charts.hallucination(overall, f"Hallucination and long output{suffix}"),
            "Hallucinated tokens per reference token, and share of clips where the output is far longer than the "
            "reference.",
        )
    )
    if (agg["slice_kind"] == "duration").any():
        builders.append(
            (
                "by_duration",
                lambda: charts.by_duration(agg, data.metric, f"Error rate by clip duration{suffix}"),
                f"Grey points are buckets {charts.TOO_SMALL_LABEL}.",
            )
        )
    blocks = []
    for name, build, caption in builders:
        spec = charts.to_spec(build())
        fname = f"{name}_{tier}"
        charts.export_png(spec, [d / f"{fname}.png" for d in out_dirs])
        blocks.append(Block("chart", name=fname, spec=spec, caption=caption, text=fname.replace("_", " ")))
    return blocks


def tier_section(data: RunData, tier: str, out_dirs: list[Path]) -> list[Block]:
    blocks = [Block("h2", TIER_TITLES.get(tier, tier))]
    if tier != "verified":
        blocks.append(
            Block(
                "p",
                "These numbers are scored against the creators' own captions, which are not checked and may "
                "not be verbatim. Read them as agreement with the creator, not as accuracy.",
                css="note",
            )
        )
    blocks.append(
        headline_table(
            data.aggregates, tier, data.metric, ranked=data.published, latency_note=latency_mode(data.meta)["text"]
        )
    )
    cap = caption_table(data.aggregates, tier, data.metric)
    blocks.append(Block("h3", "Caption metrics"))
    if cap:
        blocks.append(cap)
    else:
        blocks.append(
            Block(
                "p",
                "Not measured on this tier: timing, hallucination over non-speech, caption readiness and "
                "code-switch scores need references with word timings, marked non-speech spans, punctuation "
                f"or code-switch spans, and these references have none. Definitions: {CAPTION_METRICS_DOC}.",
                css="note",
            )
        )
    guessed, with_cat = data.category_guesses if tier != "verified" else (0, 0)
    for kind, title in (("category", "By content category"), ("audio_condition", "Music vs no music")):
        tbl = slice_table(data.aggregates, tier, data.metric, kind)
        if tbl:
            if kind == "category" and guessed:
                tbl.caption = (
                    f"Categories of {guessed} of {with_cat} clips are guesses from the channel in the clip "
                    "manifest, not a reviewer's label. " + tbl.caption
                )
            blocks.append(Block("h3", title))
            blocks.append(tbl)
    blocks.extend(chart_blocks(data, tier, out_dirs))
    return blocks


def _count(x: Any) -> int:
    return 0 if _nan(x) else int(x)


def ms(x: Any) -> str:
    return "n/a" if _nan(x) else f"{float(x):.0f} ms"


def caption_table(agg: pd.DataFrame, tier: str, metric: str) -> Block | None:
    """Timing, hallucination over non-speech, caption readiness and code-switch per provider (slice all).
    Missing measurements are n/a, never 0: a provider without word timings has no timing numbers."""
    if "timing_n_clips" not in agg:
        return None
    rows = agg[(agg["tier"] == tier) & (agg["metric"] == metric) & (agg["slice_kind"] == "all")]
    rows = rows.sort_values("provider")
    if rows.empty:
        return None
    counts = ("timing_n_clips", "nonspeech_n_clips", "readiness_n_clips", "codeswitch_n_clips")
    if not any(_count(r.get(k)) for _, r in rows.iterrows() for k in counts):
        return None  # nothing measured for any provider: a table of n/a says nothing
    body = []
    for _, r in rows.iterrows():
        tn, nn, rn, cn = (_count(r.get(k)) for k in ("timing_n_clips", "nonspeech_n_clips", "readiness_n_clips",
                                                       "codeswitch_n_clips"))  # fmt: skip
        hal = r.get("halluc_per_nonspeech_min")
        body.append(
            [
                str(r["provider"]),
                f"{ms(r.get('timing_median_ms'))} / {ms(r.get('timing_p90_ms'))}" if tn else "n/a",
                pct(r.get("timing_within_200ms")) if tn else "n/a",
                str(tn),
                "n/a" if not nn or _nan(hal) else f"{float(hal):.2f}",
                str(nn),
                num(r.get("punct_f1"), 2) if rn else "n/a",
                pct(r.get("casing_acc")) if rn else "n/a",
                str(rn),
                pct(r.get("codeswitch_error_rate")) if cn else "n/a",
                str(cn),
            ]
        )
    return Block(
        "table",
        headers=[
            "provider",
            "timing median / p90",
            "words within 200 ms",
            "timed clips",
            "halluc. words / non-speech min",
            "non-speech clips",
            "punct. F1",
            "casing acc.",
            "readiness clips",
            f"code-switch {metric}",
            "code-switch clips",
        ],  # fmt: skip
        rows=body,
        caption=f"Tier: {tier}. n/a means the measurement does not exist for that provider or clip set (for example "
        f"no word timings), not zero. Definitions: {CAPTION_METRICS_DOC}. "
        f"(source: aggregates.parquet, tier={tier}, metric={metric}, slice=all)",
    )


def slice_table(agg: pd.DataFrame, tier: str, metric: str, kind: str) -> Block | None:
    sub = agg[(agg["tier"] == tier) & (agg["metric"] == metric) & (agg["slice_kind"] == kind)]
    if sub.empty:
        return None
    slices = sorted(set(sub["slice"].astype(str)))
    providers = sorted(set(sub["provider"].astype(str)))
    body = []
    for pid in providers:
        row = [pid]
        for sl in slices:
            r = sub[(sub["provider"] == pid) & (sub["slice"] == sl)]
            if r.empty:
                row.append("n/a")
                continue
            r = r.iloc[0]
            cell = f"{pct(r['error_rate'])} (n={int(r['n_clips'])})"
            if bool(r["too_small"]):
                cell += f", {charts.TOO_SMALL_LABEL}"
            row.append(cell)
        body.append(row)
    return Block(
        "table",
        headers=["provider", *slices],
        rows=body,
        caption=f"{metric} per {kind.replace('_', ' ')}, lower is better. Slices under {rules.MIN_SLICE_CLIPS} clips "
        f"are {charts.TOO_SMALL_LABEL}. (source: aggregates.parquet, tier={tier}, metric={metric}, slice_kind={kind})",
    )


def error_blocks(errors: dict | None) -> list[Block]:
    if not errors:
        return [
            Block("h2", "Error examples"),
            Block("p", "No errors.json for this run. Run `clipbench errors`."),
        ]
    blocks = [Block("h2", "Error examples")]
    sub_rows = []
    for pid, info in sorted(errors.get("providers", {}).items()):
        top = info.get("top_substitutions", [])[:5]
        sub_rows.append([pid, "; ".join(f"{r} -> {h} (x{n})" for r, h, n in top) or "none"])
    blocks.append(
        Block(
            "table",
            headers=["provider", "most common substitutions (reference -> output)"],
            rows=sub_rows,
            caption="Normalised tokens of the primary metric; for Japanese and Korean consecutive characters are "
            "grouped into spans. (source: errors.json)",
        )
    )
    ex_rows = [
        [e["provider"], e["video_id"], e["ref_snippet"], e["hyp_snippet"]] for e in errors.get("examples", [])
    ]
    if ex_rows:
        blocks.append(
            Block(
                "table",
                headers=["provider", "clip", "reference (snippet)", "output (snippet)"],
                rows=ex_rows,
                caption="Short context around one error, never a full caption. (source: errors.json)",
            )
        )
    n_fail = len(errors.get("all_fail", []))
    thr = errors.get("all_fail_threshold", 0.5)
    blocks.append(
        Block(
            "p",
            f"{n_fail} {'clip' if n_fail == 1 else 'clips'} had every provider above {_pct_short(thr)} error. Those usually mean a bad reference "
            "and are queued for re-review rather than counted as model failures. (source: errors.json)",
        )
    )
    return blocks


def limitations(data: RunData) -> Block:
    n = {}
    overall = data.aggregates[
        (data.aggregates["slice_kind"] == "all") & (data.aggregates["metric"] == data.metric)
    ]
    for tier, grp in overall.groupby("tier"):
        n[tier] = int(grp["n_clips"].max())
    sizes = ", ".join(f"{c} {t}-tier clips" for t, c in sorted(n.items())) or "unknown"
    lang = LANGUAGE_NAMES.get(data.language, data.language)
    pmeta = provider_meta(data.meta)
    if not pmeta:
        toml = _pricing_toml()
        models = overall[["provider", "model_id"]].drop_duplicates()
        pmeta = [
            {
                "id": r.provider,
                "model_id": r.model_id,
                "checked_on": toml.get(r.provider, {}).get("checked_on"),
            }
            for r in models.itertuples()
        ]
    pmeta = sorted(pmeta, key=lambda p: str(p.get("id")))
    dates = {p.get("checked_on") for p in pmeta}
    if len(dates) == 1 and None not in dates:
        model_list = "; ".join(f"{p['id']} = {p['model_id']}" for p in pmeta) + f" (prices checked {dates.pop()})"
    else:
        model_list = "; ".join(
            f"{p['id']} = {p['model_id']} (price checked {p['checked_on'] or 'not recorded'})" for p in pmeta
        )
    present = set(overall["provider"].astype(str)) | set(data.run_counts)
    under_test = [name for pid, name in (("youtube-auto", "YouTube auto captions"), ("wayin", "Wayin"))
                  if pid in present]  # fmt: skip
    return Block(
        "list",
        items=[
            f"Sample size: {sizes}. Confidence intervals are wide at this size; slices under the minimum are marked "
            f"{charts.TOO_SMALL_LABEL}.",
            f"One language: {lang} ({data.language}). Results do not transfer to other languages.",
            "Creator captions may not be verbatim (edited, shortened, or translated), which is why the creator tier is "
            "reported separately and labelled unverified.",
            f"Providers change models without notice. Model ids in this run: {model_list or 'n/a'}.",
            "Prices are list prices as of each checked date above, not what any customer actually pays.",
            *(
                [f"{' and '.join(under_test)} {'is' if under_test == ['Wayin'] else 'are'} "
                 f"{'systems' if len(under_test) > 1 else 'a system'} under test, never a reference."]  # fmt: skip
                if under_test
                else []
            ),
            *(
                [
                    "Many creators start their captions from YouTube's own auto captions, so against the creator "
                    "tier YouTube auto captions are favoured; only the verified tier, corrected by ear, is a fair "
                    "reference."
                ]
                if "youtube-auto" in present and "creator" in n
                else []
            ),
            latency_mode(data.meta)["text"]
            + " Latency is wall-clock from one machine and network, so it shows relative speed, not what you will "
            "see.",
            *(
                [
                    "Not every provider has a result on every clip, so each provider's numbers cover only its "
                    "own clips (paired tests use the clips both providers scored): "
                    + "; ".join(data.coverage.values())
                    + "."
                ]
                if data.coverage
                else []
            ),
        ],
    )


def v2_findings(data: RunData) -> list[dict]:
    """Structured findings for a published language; none otherwise. Verified tier only."""
    if not data.published:
        return []
    return findings.structured_findings(
        data.aggregates,
        data.paired,
        data.metric,
        tier="verified",
        min_clips=rules.MIN_SLICE_CLIPS,
        eligible=data.eligible,
    )


def source_text(source: dict) -> str:
    filt = ", ".join(f"{k}={v}" for k, v in (source.get("filters") or {}).items())
    return f"(source: {source.get('table')}.parquet{', ' + filt if filt else ''})"


def unpublished_text(data: RunData) -> str:
    st = data.status or rules.LanguageStatus(lang=data.language)
    div = st.verified_diversity or {}
    problems = "; ".join(div.get("problems") or []) or "passes"
    return (
        f"Not published yet: {st.verified_clips} of {rules.MIN_VERIFIED_CLIPS} verified clips, "
        f"{st.providers_ok} of {rules.MIN_PROVIDERS_OK} eligible providers (each with results on at least "
        f"{st.min_coverage:.0%} of the verified clips), and verified-set diversity: {problems}. Until all three "
        "are met there is no ranking and no findings; the numbers here are a progress check of the pipeline."
    )


def excluded_text(data: RunData) -> str | None:
    st = data.status
    if st is None or not st.providers_excluded:
        return None
    # Without verified clips nobody has coverage; the funnel and the unpublished note already say so.
    parts = [
        f"{e['provider']}: {e['reason']}" for e in st.providers_excluded if e.get("coverage") is not None
    ]
    if not parts:
        return None
    return (
        "Excluded from every ranking, comparison and finding because they cover too few verified clips: "
        + "; ".join(parts)
        + "."
    )


def funnel_block(data: RunData) -> Block:
    """Sampled, audio available, reviewed, skipped, unusable, verified, then scored per provider."""
    rows: list[list[str]] = []
    f = data.funnel
    labels = [("sampled", "sampled"), ("audio_available", "audio available"), ("reviewed", "reviewed"),
              ("skipped", "skipped by the reviewer"), ("unusable", "marked unusable"), ("verified", "verified")]  # fmt: skip
    for key, label in labels:
        rows.append([label, "n/a" if f is None else str(f.get(key, 0))])
    st = data.status
    cover = st.provider_coverage if st else {}
    no_verified = all(pc.n_eval == 0 for pc in cover.values())
    if no_verified and data.run_counts:
        # Nothing verified yet: coverage on the verified set is 0 of 0 for everyone, which says nothing.
        # Show what each provider returned on the clips this run gave it instead.
        for pid, (ok, total) in sorted(data.run_counts.items()):
            rows.append([f"results: {pid}", f"{ok} of {total} clips in this run (not eligible: nothing verified)"])
        tail = (
            "No clip is verified yet, so no provider can be eligible; the per provider lines count ok results on "
            "every clip of this run (unverified references included)."
        )
    else:
        for pid, pc in sorted(cover.items()):
            cov = "n/a" if pc.coverage is None else f"{pc.coverage:.0%}"
            state = "eligible" if pc.eligible else "excluded"
            rows.append([f"scored: {pid}", f"{pc.n_scored} of {pc.n_eval} verified ({cov}, {state})"])
        tail = "Per provider lines count ok results on the verified evaluation clips of this run."
    need = f"A provider needs {st.min_coverage if st else 0.9:.0%} of the verified clips to be eligible."
    na = " n/a: no local sample to count from." if f is None else ""
    return Block(
        "table",
        headers=["stage", "clips"],
        rows=rows,
        caption=f"Dataset funnel for this language. {tail} {need}{na} "
        "(source: data/sample.parquet, verified files, rows.parquet)",
    )


def status_block(data: RunData) -> Block:
    st = data.status or rules.LanguageStatus(lang=data.language)
    lang = LANGUAGE_NAMES.get(data.language, data.language)
    if data.published:
        div = st.verified_diversity or {}
        return Block(
            "p",
            f"{lang} is published: {st.verified_clips} verified clips, {st.providers_ok} eligible providers, "
            f"{div.get('channels', 0)} channels and {len(div.get('categories') or [])} categories among the "
            "verified clips.",
            css="note",
        )
    return Block("p", unpublished_text(data), css="note")


def meta_block(data: RunData) -> Block:
    m = data.meta
    commit = m.get("git_commit") or m.get("commit") or "n/a"
    if m.get("git_dirty"):
        commit = f"{commit} (dirty)"
    date = m.get("updated_at") or m.get("created_at") or m.get("date") or "n/a"
    seed = m.get("sample_seed", m.get("seed", "n/a"))
    return Block(
        "list",
        items=[
            f"run id: {data.run_id}",
            f"commit: {commit}",
            f"date: {date}",
            f"sample seed: {seed}; stability seed: {m.get('stability_seed', 'n/a')}",
            f"language: {data.language}; primary metric: {data.metric}",
            f"latency: {latency_mode(m)['short']}",
        ],
    )


def build_blocks(data: RunData, out_dirs: list[Path]) -> list[Block]:
    tiers = [t for t in ("verified", "creator") if t in set(data.aggregates["tier"])]
    blocks: list[Block] = []
    head = tiers[0] if tiers else "verified"
    lang = LANGUAGE_NAMES.get(data.language, data.language)
    blocks.append(
        Block(
            "p",
            f"Speech-to-text models on real {lang} short-form video ({_clip_sources(data.scores)}). "
            f"Headline tier: {head}. "
            f"Primary metric: {data.metric}, lower is better.",
            css="lede",
        )
    )
    blocks.append(status_block(data))
    excl = excluded_text(data)
    if excl:
        blocks.append(Block("p", excl, css="note"))
    blocks.append(Block("h3", "Dataset funnel"))
    blocks.append(funnel_block(data))
    for tier in tiers:
        blocks.extend(tier_section(data, tier, out_dirs))

    blocks.append(Block("h2", "Findings"))
    if not data.published:
        blocks.append(Block("p", unpublished_text(data), css="note"))
    else:
        blocks.append(
            Block(
                "p",
                "Drafted automatically from the tables. Each line must be checked by a person before it is "
                "quoted. Only a paired test verdict can say one provider beats another.",
                css="note",
            )
        )
        for tier in tiers:
            lines = findings.draft_findings(
                data.aggregates, data.paired, data.stability, tier, data.metric, eligible=data.eligible
            )
            if lines:
                blocks.append(Block("h3", TIER_TITLES.get(tier, tier)))
                blocks.append(Block("list", items=lines, css="findings"))
        v2 = v2_findings(data)
        if v2:
            blocks.append(Block("h3", "Caption findings"))
            blocks.append(
                Block(
                    "list",
                    items=[f"{findings.PREFIX} {f['text']} {source_text(f['source'])}" for f in v2],
                    css="findings",
                )
            )

    blocks.extend(error_blocks(data.errors))
    blocks.append(Block("h2", "Limitations"))
    blocks.append(limitations(data))
    blocks.append(Block("h2", "Methodology"))
    url = methodology_url()
    blocks.append(
        Block(
            "p",
            f"How references, normalisation, metrics and statistics work: {url or METHODOLOGY_DOC} "
            "in the clipbench repository.",
            name=url or "",
        )
    )
    blocks.append(Block("h2", "Run"))
    blocks.append(meta_block(data))
    return blocks


# ---------------------------------------------------------------- markdown


def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: RunData, blocks: list[Block]) -> str:
    out = [f"# clipbench report: {data.run_id}", ""]
    for b in blocks:
        if b.kind == "h2":
            out += [f"## {b.text}", ""]
        elif b.kind == "h3":
            out += [f"### {b.text}", ""]
        elif b.kind == "p":
            text = b.text
            if b.name and b.name.startswith("http"):
                text = text.replace(b.name, f"[{b.name}]({b.name})")
            elif METHODOLOGY_DOC in text:
                text = text.replace(METHODOLOGY_DOC, f"[{METHODOLOGY_DOC}](../../{METHODOLOGY_DOC})")
            out += [text, ""]
        elif b.kind == "list":
            out += [f"- {i}" for i in b.items] + [""]
        elif b.kind == "table":
            out.append("| " + " | ".join(_md_cell(h) for h in b.headers) + " |")
            out.append("|" + "|".join("---" for _ in b.headers) + "|")
            out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in b.rows]
            out += ["", f"_{b.caption}_" if b.caption else "", ""]
        elif b.kind == "chart":
            out += [f"![{b.text}](charts/{b.name}.png)", "", f"_{b.caption}_", ""]
    return "\n".join(out).rstrip() + "\n"


def build_report(run_id: str, settings: Settings | None = None) -> tuple[Path, Path]:
    from clipbench.report import site

    settings = settings or get_settings()
    data = load_run(run_id, settings)
    res_dir = settings.results_dir / run_id
    site_dir = settings.site_dir
    blocks = build_blocks(data, [res_dir / "charts", site_dir / "charts"])
    md_path = res_dir / "report.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(data, blocks))
    html_path = site_dir / "index.html"
    site_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(site.render_html(data, blocks))
    return md_path, html_path
