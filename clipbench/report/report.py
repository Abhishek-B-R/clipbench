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
from clipbench.report import charts, findings

TIER_TITLES = {"verified": "Verified references", "creator": "Creator captions (unverified reference)"}
FALLBACK_PRIMARY = {"ja": "cer", "es": "wer", "ko": "cer_nospace"}
LANGUAGE_NAMES = {"ja": "Japanese", "es": "Spanish", "ko": "Korean"}
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
    language = str(
        meta.get("language")
        or (scores["language"].iloc[0] if scores is not None and len(scores) else "")
        or settings.language
    ).split("-")[0]
    return RunData(
        run_id=run_id,
        language=language,
        metric=_primary_metric(language, scores, aggregates),
        aggregates=aggregates,
        paired=_read(res / "paired.parquet"),
        stability=_read(res / "stability.parquet"),
        scores=scores,
        errors=errors,
        meta=meta,
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


def headline_table(agg: pd.DataFrame, tier: str, metric: str) -> Block:
    rows = agg[(agg["tier"] == tier) & (agg["metric"] == metric) & (agg["slice_kind"] == "all")]
    rows = rows.sort_values("error_rate", na_position="last")
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
        caption=f"Tier: {tier}. Metric: {metric}, lower is better. Slice: all clips.{label} "
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
    blocks.append(headline_table(data.aggregates, tier, data.metric))
    blocks.extend(chart_blocks(data, tier, out_dirs))
    return blocks


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
            f"{n_fail} {'clip' if n_fail == 1 else 'clips'} had every provider above {pct(thr)} error. Those usually mean a bad reference "
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
    sizes = ", ".join(f"{t}: {c} clips" for t, c in sorted(n.items())) or "unknown"
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
    model_list = "; ".join(
        f"{p['id']} = {p['model_id']} (price checked {p['checked_on'] or 'n/a'})" for p in pmeta
    )
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
            "YouTube auto captions and Wayin are systems under test, never references.",
        ],
    )


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
            f"Speech-to-text models on real {lang} short-form video (YouTube Shorts). Headline tier: {head}. "
            f"Primary metric: {data.metric}, lower is better.",
            css="lede",
        )
    )
    for tier in tiers:
        blocks.extend(tier_section(data, tier, out_dirs))

    blocks.append(Block("h2", "Findings"))
    blocks.append(
        Block(
            "p",
            "Drafted automatically from the tables. Each line must be checked by a person before it is "
            "quoted. Only a paired test verdict can say one provider beats another.",
            css="note",
        )
    )
    for tier in tiers:
        lines = findings.draft_findings(data.aggregates, data.paired, data.stability, tier, data.metric)
        if lines:
            blocks.append(Block("h3", TIER_TITLES.get(tier, tier)))
            blocks.append(Block("list", items=lines, css="findings"))

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
