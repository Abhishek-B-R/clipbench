"""Render the report to one static page: site/index.html.

Design notes and design references: docs/site_design.md. In short: a hero with the one-line answer and
KPI tiles, a sortable leaderboard with inline CI bars, a provider x clip-type heatmap, chart cards,
numbered DRAFT findings, error chips, limitations and run meta. One segmented control switches the
tier (verified headline, creator labelled "unverified reference"); tiers are never shown mixed.

No server: the page works opened from disk and deployed to Cloudflare Pages as is. Vanilla inline JS
only. Chart specs are inlined and drawn by vega-embed from pinned jsdelivr versions; each chart starts
as its PNG export (site/charts/*.png) so every chart still shows offline or without JavaScript.
Without JavaScript every tier and metric panel is shown, each with its own label.
"""

from __future__ import annotations

import html
import json
import math
from typing import Any

import pandas as pd

from clipbench.report import charts, findings
from clipbench.report.report import (
    LANGUAGE_NAMES,
    METHODOLOGY_DOC,
    Block,
    RunData,
    limitations,
    meta_block,
    methodology_url,
)

VEGA = "https://cdn.jsdelivr.net/npm/vega@5.30.0"
VEGA_LITE = "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0"
VEGA_EMBED = "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0"

TIER_LABEL = {"verified": "Verified", "creator": "Creator captions"}
TIER_NOTE = {
    "verified": "Scored against human-verified transcripts.",
    "creator": "Unverified reference: scored against the creators' own captions, which may not be verbatim. "
    "Read as agreement with the creator, not accuracy.",
}
CHART_TITLES = {
    "accuracy_vs_cost": ("Accuracy vs cost", "wide"),
    "by_tag": ("Error rate by clip type", ""),
    "hallucination": ("Hallucination and long output", ""),
    "by_duration": ("Error rate by clip duration", "wide"),
}
RAMP_STEPS = 5

CSS = """
:root {
  color-scheme: light;
  --bg: #f7f7f5; --surface: #ffffff; --surface-2: #f1f0ec; --text: #0b0b0b; --muted: #52514e;
  --faint: #7d7c76; --border: #e4e3de; --border-strong: #cfcec8; --accent: #2a78d6;
  --accent-soft: #e8f1fb; --draft-bg: #fff4d6; --draft-text: #6b4b00; --draft-border: #f0d48a;
  --warn-bg: #fdf0e8; --warn-text: #8a3b12; --good: #0b7a43;
  --h0: #eef4fb; --h1: #cfe1f6; --h2: #9cc2ee; --h3: #5a9ae0; --h4: #2a6fc0;
  --h-ink-light: #0b0b0b; --h-ink-dark: #ffffff;
  --hatch: repeating-linear-gradient(135deg, #ecebe6 0 6px, #f7f7f5 6px 12px);
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 1px 1px rgba(0,0,0,.03);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #111110; --surface: #1a1a19; --surface-2: #222220; --text: #f4f3ee; --muted: #c3c2b7;
    --faint: #8f8e86; --border: #2c2b28; --border-strong: #3c3b37; --accent: #5b9ff0;
    --accent-soft: #1b2a3d; --draft-bg: #2a2210; --draft-text: #f0c66a; --draft-border: #4d3d14;
    --warn-bg: #2d1c12; --warn-text: #f3a77a; --good: #4cc38a;
    --h0: #1b2430; --h1: #1e3550; --h2: #224d7a; --h3: #2b69aa; --h4: #3f8ae0;
    --h-ink-light: #f4f3ee; --h-ink-dark: #ffffff;
    --hatch: repeating-linear-gradient(135deg, #262624 0 6px, #1a1a19 6px 12px);
    --shadow: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #111110; --surface: #1a1a19; --surface-2: #222220; --text: #f4f3ee; --muted: #c3c2b7;
  --faint: #8f8e86; --border: #2c2b28; --border-strong: #3c3b37; --accent: #5b9ff0;
  --accent-soft: #1b2a3d; --draft-bg: #2a2210; --draft-text: #f0c66a; --draft-border: #4d3d14;
  --warn-bg: #2d1c12; --warn-text: #f3a77a; --good: #4cc38a;
  --h0: #1b2430; --h1: #1e3550; --h2: #224d7a; --h3: #2b69aa; --h4: #3f8ae0;
  --h-ink-light: #f4f3ee; --h-ink-dark: #ffffff;
  --hatch: repeating-linear-gradient(135deg, #262624 0 6px, #1a1a19 6px 12px);
  --shadow: none;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-padding-top: 72px; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
.mono, code, .num { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.num { font-variant-numeric: tabular-nums; }
a { color: var(--accent); }
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 16px; }

/* top bar */
.topbar { position: sticky; top: 0; z-index: 10; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--border); }
.topbar .wrap { display: flex; align-items: center; gap: 16px; height: 52px; }
.brand { font-weight: 700; letter-spacing: -0.01em; white-space: nowrap; color: var(--text); text-decoration: none; }
.brand span { color: var(--faint); font-weight: 500; }
.toc { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; flex: 1; min-width: 0; }
.toc::-webkit-scrollbar { display: none; }
.toc a { color: var(--muted); text-decoration: none; padding: 6px 10px; border-radius: 6px; font-size: 13px;
  white-space: nowrap; }
.toc a:hover { background: var(--surface-2); color: var(--text); }
.toc a.on { background: var(--surface-2); color: var(--text); font-weight: 600; }
#theme { flex: none; font: inherit; font-size: 12px; background: var(--surface); color: var(--muted);
  border: 1px solid var(--border); border-radius: 999px; padding: 4px 10px; cursor: pointer; }

/* hero */
.hero { padding-top: 40px; padding-bottom: 8px; }
.eyebrow { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--faint); margin: 0 0 10px; }
.hero h1 { font-size: clamp(26px, 4.4vw, 40px); line-height: 1.12; letter-spacing: -0.02em; margin: 0 0 12px;
  max-width: 24ch; }
.hero h1 em { font-style: normal; color: var(--accent); }
.hero .hero-sub { color: var(--muted); max-width: 70ch; margin: 0 0 6px; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 24px 0 16px; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  box-shadow: var(--shadow); min-width: 0; }
.kpi .label { font-size: 12px; color: var(--faint); margin: 0 0 6px; }
.kpi .value { font-size: 26px; font-weight: 650; letter-spacing: -0.02em; line-height: 1.1; margin: 0; }
.kpi .who { font-size: 13px; color: var(--muted); margin: 6px 0 0; overflow-wrap: anywhere; }
.kpi .src { font-size: 11px; color: var(--faint); margin: 8px 0 0; overflow-wrap: anywhere; }
.chips-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0 0; }
.chips-row .label { font-size: 12px; color: var(--faint); margin-right: 4px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 13px;
  background: var(--surface); border: 1px solid var(--border); white-space: nowrap; }
.chip.small { color: var(--faint); background: var(--hatch); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; display: inline-block; }

/* sections */
section { padding: 36px 0 4px; }
h2 { font-size: 20px; letter-spacing: -0.01em; margin: 0 0 4px; }
.lead { color: var(--muted); margin: 0 0 16px; max-width: 75ch; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0 0 14px; }
.seg { display: inline-flex; background: var(--surface-2); border: 1px solid var(--border); border-radius: 9px; padding: 3px; }
.seg button { font: inherit; font-size: 13px; border: 0; background: transparent; color: var(--muted);
  padding: 5px 12px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--surface); color: var(--text); font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.no-js .seg { display: none; }
.badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
  vertical-align: middle; white-space: nowrap; }
.badge.unverified { background: var(--warn-bg); color: var(--warn-text); }
.badge.verified { background: var(--accent-soft); color: var(--accent); }
.badge.draft { background: var(--draft-bg); color: var(--draft-text); border: 1px solid var(--draft-border); }
.panel-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 8px; font-size: 13px; color: var(--muted); }
.js [data-panel][hidden] { display: none !important; }

/* cards and tables */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow); }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 14px; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
th { font-size: 12px; font-weight: 600; color: var(--faint); background: var(--surface-2); position: sticky; top: 0; }
th:first-child { border-top-left-radius: 12px; } th:last-child { border-top-right-radius: 12px; }
th button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
th[aria-sort="ascending"] button::after { content: " \\2191"; }
th[aria-sort="descending"] button::after { content: " \\2193"; }
td.r, th.r { text-align: right; }
.lb td.prov, .lb th.prov { position: sticky; left: 0; background: var(--surface); z-index: 1; }
.lb th.prov { background: var(--surface-2); z-index: 2; }
.lb tr.top td { background: color-mix(in srgb, var(--accent-soft) 60%, var(--surface)); }
.prov-name { font-weight: 600; display: inline-flex; align-items: center; gap: 8px; }
.model { display: block; font-size: 12px; color: var(--faint); margin-left: 16px; }
.ci { display: flex; align-items: center; gap: 10px; min-width: 200px; }
.ci .val { width: 52px; text-align: right; font-weight: 600; }
.ci .track { position: relative; flex: 1; height: 14px; min-width: 110px; }
.ci .track::before { content: ""; position: absolute; left: 0; right: 0; top: 6px; height: 2px; background: var(--surface-2); border-radius: 2px; }
.ci .whisker { position: absolute; top: 4px; height: 6px; border-radius: 3px; opacity: .35; }
.ci .pt { position: absolute; top: 2px; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%;
  box-shadow: 0 0 0 2px var(--surface); }
.ci .range { font-size: 11px; color: var(--faint); width: 92px; }
.verdict { font-size: 12px; color: var(--muted); }
.verdict.best { color: var(--good); font-weight: 600; }
.caption { font-size: 12px; color: var(--faint); margin: 8px 2px 0; }

/* heatmap */
.hm td, .hm th { text-align: center; padding: 8px 10px; }
.hm td.prov, .hm th.prov { text-align: left; position: sticky; left: 0; background: var(--surface); z-index: 1; }
.hm th.prov { background: var(--surface-2); z-index: 2; }
.hm td.cell { font-weight: 600; min-width: 84px; border-left: 2px solid var(--surface); }
.hm td.cell .n { display: block; font-size: 10px; font-weight: 500; opacity: .75; }
.hm td.cell.best { box-shadow: inset 0 0 0 2px var(--text); }
.hm td.h0 { background: var(--h0); color: var(--h-ink-light); } .hm td.h1 { background: var(--h1); color: var(--h-ink-light); }
.hm td.h2 { background: var(--h2); color: var(--h-ink-light); } .hm td.h3 { background: var(--h3); color: var(--h-ink-dark); }
.hm td.h4 { background: var(--h4); color: var(--h-ink-dark); }
.hm td.small { background: var(--hatch); color: var(--faint); font-weight: 500; }
.hm td.na { color: var(--faint); }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 12px; color: var(--faint); margin: 10px 2px 0; }
.legend .ramp { display: inline-flex; border-radius: 4px; overflow: hidden; vertical-align: middle; }
.legend .ramp i { width: 18px; height: 10px; display: block; }
.legend .sw { display: inline-block; width: 18px; height: 10px; border-radius: 3px; vertical-align: middle; margin-right: 4px; }

/* charts */
.charts { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.charts figure { margin: 0; padding: 14px 16px 12px; min-width: 0; }
.charts figure.wide { grid-column: 1 / -1; }
figure h3 { font-size: 14px; margin: 0 0 8px; }
.chart { width: 100%; overflow-x: auto; }
.chart img, .chart svg { max-width: 100%; height: auto; display: block; }
figcaption { font-size: 12px; color: var(--faint); margin-top: 8px; }

/* findings */
ol.findings { list-style: none; padding: 0; margin: 0; counter-reset: f; }
ol.findings li { counter-increment: f; display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 4px 12px;
  padding: 14px 0; border-top: 1px solid var(--border); }
ol.findings li::before { content: counter(f, decimal-leading-zero); font-family: ui-monospace, Menlo, monospace;
  color: var(--faint); font-size: 13px; padding-top: 2px; }
ol.findings .text { margin: 0; }
ol.findings .src { grid-column: 2; font-size: 11px; color: var(--faint); margin: 0; overflow-wrap: anywhere; }
.note { background: var(--draft-bg); color: var(--draft-text); border: 1px solid var(--draft-border); border-radius: 10px;
  padding: 10px 14px; font-size: 13px; margin: 0 0 14px; }

/* errors */
.err-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 16px; }
.err-card { padding: 14px 16px; min-width: 0; }
.err-card h3 { font-size: 14px; margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
.subs { display: flex; flex-wrap: wrap; gap: 6px; }
.sub { font-size: 13px; padding: 3px 8px; border-radius: 6px; background: var(--surface-2); white-space: nowrap; }
.sub b { font-weight: 500; color: var(--faint); margin-left: 4px; font-size: 11px; }
.sub .arrow { color: var(--faint); margin: 0 4px; }
.examples td.wrapcell { white-space: normal; min-width: 10ch; }

/* limits and meta */
.limits { padding: 6px 18px; }
.limits li { margin: 8px 0; }
dl.meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 20px; margin: 0; padding: 14px 18px; font-size: 14px; }
dl.meta dt { color: var(--faint); }
dl.meta dd { margin: 0; overflow-wrap: anywhere; }
footer { padding: 40px 0 56px; font-size: 12px; color: var(--faint); }

@media (max-width: 860px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .charts { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 520px) {
  .topbar .wrap { gap: 10px; }
  .brand span { display: none; }
  .kpi .value { font-size: 22px; }
  th, td { padding: 8px 10px; }
  .hero { padding-top: 28px; }
  .kpi .src { display: none; }
  .ci { min-width: 150px; gap: 8px; }
  .ci .track { min-width: 70px; }
  .ci .range { display: none; }
}
"""

JS = r"""
(function () {
  var root = document.documentElement;
  root.classList.remove('no-js'); root.classList.add('js');
  var btn = document.getElementById('theme');
  try { var saved = localStorage.getItem('clipbench-theme'); if (saved) root.dataset.theme = saved; } catch (e) {}
  function isDark() {
    return root.dataset.theme ? root.dataset.theme === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function label() { btn.textContent = isDark() ? 'Light' : 'Dark'; }
  btn.addEventListener('click', function () {
    root.dataset.theme = isDark() ? 'light' : 'dark';
    try { localStorage.setItem('clipbench-theme', root.dataset.theme); } catch (e) {}
    label();
  });
  label();

  // Segmented controls: tier is global; metric applies to the leaderboard and heatmaps.
  var state = { tier: root.dataset.defaultTier, metric: root.dataset.defaultMetric };
  function apply() {
    document.querySelectorAll('[data-panel]').forEach(function (el) {
      var okTier = !el.dataset.tier || el.dataset.tier === state.tier;
      var okMetric = !el.dataset.metric || el.dataset.metric === state.metric;
      el.hidden = !(okTier && okMetric);
    });
    document.querySelectorAll('.seg button').forEach(function (b) {
      b.setAttribute('aria-pressed', String(state[b.dataset.group] === b.dataset.value));
    });
  }
  document.querySelectorAll('.seg button').forEach(function (b) {
    b.addEventListener('click', function () { state[b.dataset.group] = b.dataset.value; apply(); });
  });
  apply();

  // Sortable leaderboard columns.
  document.querySelectorAll('table.sortable').forEach(function (table) {
    table.querySelectorAll('th[data-key] button').forEach(function (b) {
      b.addEventListener('click', function () {
        var th = b.parentElement, key = th.dataset.key;
        var dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
        table.querySelectorAll('th[data-key]').forEach(function (o) { o.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', dir);
        var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (x, y) {
          var a = x.dataset[key], c = y.dataset[key];
          var na = parseFloat(a), nc = parseFloat(c);
          var cmp = (!isNaN(na) && !isNaN(nc)) ? na - nc : String(a).localeCompare(String(c));
          if (isNaN(na) && !isNaN(nc)) cmp = 1; if (!isNaN(na) && isNaN(nc)) cmp = -1;
          return dir === 'ascending' ? cmp : -cmp;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  // Section nav highlight.
  var links = {};
  document.querySelectorAll('.toc a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && links[e.target.id]) {
          Object.keys(links).forEach(function (k) { links[k].classList.remove('on'); });
          links[e.target.id].classList.add('on');
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    document.querySelectorAll('section[id], header[id]').forEach(function (s) { io.observe(s); });
  }

  // Charts: replace each PNG with the interactive chart when vega-embed is available.
  if (typeof vegaEmbed !== 'function') return;
  var specs = JSON.parse(document.getElementById('chart-specs').textContent);
  Object.keys(specs).forEach(function (name) {
    var el = document.getElementById('chart-' + name);
    if (!el) return;
    var holder = document.createElement('div');
    vegaEmbed(holder, specs[name], { actions: false, renderer: 'svg' })
      .then(function () { el.innerHTML = ''; el.appendChild(holder); })
      .catch(function () { /* keep the PNG */ });
  });
})();
"""


# ---------------------------------------------------------------- helpers


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _nan(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return False


def pct(x: Any) -> str:
    return "n/a" if _nan(x) else f"{float(x) * 100:.1f}%"


def money(x: Any) -> str:
    if _nan(x):
        return "n/a"
    return "$0" if float(x) == 0 else f"${float(x):.2f}"


def secs(ms: Any) -> str:
    return "n/a" if _nan(ms) else f"{float(ms) / 1000:.2f} s"


def _sortval(x: Any) -> str:
    return "" if _nan(x) else f"{float(x):.6g}"


def _draft(text: str) -> str:
    """Escape a finding, showing its "DRAFT: verify." prefix as a badge (the words stay in the page)."""
    prefix = findings.PREFIX
    if text.startswith(prefix):
        return f'<span class="badge draft">{_e(prefix)}</span> {_e(text[len(prefix) :].lstrip())}'
    return _e(text)


def _split_source(line: str) -> tuple[str, str]:
    idx = line.rfind(" (source: ")
    if idx == -1:
        return line, ""
    return line[:idx].rstrip(".") + ".", line[idx + 2 :].rstrip(".").rstrip(")")


class Ctx:
    """Everything the page needs, computed once from the run data."""

    def __init__(self, data: RunData, blocks: list[Block]):
        self.data = data
        agg = data.aggregates
        self.tiers = [t for t in ("verified", "creator") if t in set(agg["tier"])]
        self.head = self.tiers[0] if self.tiers else "verified"
        metrics = list(dict.fromkeys(agg["metric"]))
        self.metrics = [data.metric] + [m for m in metrics if m != data.metric]
        providers = sorted(set(agg["provider"]))
        self.color = {p: charts.PALETTE[i % len(charts.PALETTE)] for i, p in enumerate(providers)}
        self.charts = [b for b in blocks if b.kind == "chart"]
        self.lang = LANGUAGE_NAMES.get(data.language, data.language)

    def overall(self, tier: str, metric: str) -> pd.DataFrame:
        a = self.data.aggregates
        rows = a[(a["tier"] == tier) & (a["metric"] == metric) & (a["slice_kind"] == "all")]
        return rows.sort_values("error_rate", na_position="last")

    def dot(self, provider: str) -> str:
        return f'<span class="dot" style="background:{self.color.get(provider, "#888")}"></span>'


def _tier_badge(tier: str) -> str:
    if tier == "verified":
        return '<span class="badge verified">verified reference</span>'
    return '<span class="badge unverified">unverified reference</span>'


def _seg(group: str, options: list[tuple[str, str]], default: str, label: str) -> str:
    buttons = "".join(
        f'<button type="button" data-group="{group}" data-value="{_e(v)}" '
        f'aria-pressed="{str(v == default).lower()}">{_e(t)}</button>'
        for v, t in options
    )
    return f'<div class="seg" role="group" aria-label="{_e(label)}">{buttons}</div>'


def _controls(ctx: Ctx, with_metric: bool) -> str:
    parts = []
    if len(ctx.tiers) > 1:
        parts.append(_seg("tier", [(t, TIER_LABEL.get(t, t)) for t in ctx.tiers], ctx.head, "Reference tier"))
    if with_metric and len(ctx.metrics) > 1:
        parts.append(_seg("metric", [(m, m) for m in ctx.metrics], ctx.data.metric, "Metric"))
    return f'<div class="controls">{"".join(parts)}</div>' if parts else ""


# ---------------------------------------------------------------- sections


def hero(ctx: Ctx) -> str:
    d = ctx.data
    tier, metric = ctx.head, d.metric
    ov = ctx.overall(tier, metric).dropna(subset=["error_rate"])
    eyebrow = f"Speech-to-text benchmark / {ctx.lang} short-form video / run {d.run_id}"
    if ov.empty:
        return (
            f'<header class="hero wrap" id="top"><p class="eyebrow">{_e(eyebrow)}</p>'
            f"<h1>No scored clips in this run yet.</h1></header>"
        )
    best = ov.iloc[0]
    tier_word = "verified clips" if tier == "verified" else "creator captions (unverified reference)"
    h1 = (
        f"Lowest {_e(metric.upper())} on {_e(tier_word)}: <em>{_e(best['provider'])}</em>, "
        f"{_e(pct(best['error_rate']))}."
    )
    src_all = f"source: aggregates.parquet, tier={tier}, metric={metric}, slice=all"

    sub = ""
    if len(ov) > 1:
        line = findings.compare_sentence(
            d.paired, tier, metric, "all", "all", best["provider"], ov.iloc[1]["provider"], "overall"
        )
        text, src = _split_source(line)
        sub = (
            f'<p class="hero-sub">{_draft(text)} '
            f'<span class="mono" style="font-size:11px;color:var(--faint)">({_e(src)})</span></p>'
        )

    # KPI tiles.
    tiles = [
        (
            f"Lowest {metric}",
            pct(best["error_rate"]),
            f"{best['provider']} ({best['model_id']}), 95% CI {pct(best['ci_low'])} to {pct(best['ci_high'])}",
            src_all,
        )
    ]
    within = ov[ov["error_rate"] <= best["ci_high"]].copy()
    within["c"] = pd.to_numeric(within["cost_per_hour"], errors="coerce")
    within = within.dropna(subset=["c"]).sort_values(["c", "error_rate"])
    if len(within):
        c = within.iloc[0]
        tiles.append(
            (
                "Cheapest inside best's CI",
                money(c["c"]) + " / h",
                f"{c['provider']}, {pct(c['error_rate'])}",
                src_all,
            )
        )
    lat = ov.dropna(subset=["latency_p50"]).sort_values("latency_p50")
    if len(lat):
        q = lat.iloc[0]
        tiles.append(("Fastest median latency", secs(q["latency_p50"]), f"{q['provider']} per clip", src_all))
    sizes = []
    for t in ctx.tiers:
        o = ctx.overall(t, metric)
        if len(o):
            sizes.append(f"{int(o['n_clips'].max())} {t}")
    tiles.append(
        (
            "Clips scored",
            sizes[0].split()[0] if sizes else "0",
            " / ".join(sizes),
            "source: aggregates.parquet, n_clips",
        )
    )
    kpis = "".join(
        f'<div class="kpi"><p class="label">{_e(lbl)}</p><p class="value num">{_e(val)}</p>'
        f'<p class="who">{_e(who)}</p><p class="src mono">{_e(src)}</p></div>'
        for lbl, val, who, src in tiles
    )

    # Best by clip type strip.
    a = d.aggregates
    tags = a[(a["tier"] == tier) & (a["metric"] == metric) & (a["slice_kind"] == "tag")]
    chips = []
    for slice_, grp in tags.groupby("slice", sort=True):
        usable = grp[~grp["too_small"].astype(bool)].dropna(subset=["error_rate"]).sort_values("error_rate")
        if usable.empty:
            n = int(grp["n_clips"].max())
            chips.append(f'<span class="chip small">{_e(slice_)}: too small to conclude (n={n})</span>')
        else:
            w = usable.iloc[0]
            chips.append(
                f'<span class="chip">{ctx.dot(w["provider"])}{_e(slice_)}: <b>{_e(w["provider"])}</b> '
                f'<span class="num">{_e(pct(w["error_rate"]))}</span></span>'
            )
    strip = ""
    if chips:
        strip = f'<div class="chips-row"><span class="label">Lowest by clip type</span>{"".join(chips)}</div>'
    return (
        f'<header class="hero wrap" id="top"><p class="eyebrow">{_e(eyebrow)}</p><h1>{h1}</h1>{sub}'
        f'<div class="kpis">{kpis}</div>{strip}</header>'
    )


def leaderboard(ctx: Ctx) -> str:
    d = ctx.data
    panels = []
    for tier in ctx.tiers:
        for metric in ctx.metrics:
            ov = ctx.overall(tier, metric)
            if ov.empty:
                continue
            scale = max(
                float(ov["ci_high"].max() if not ov["ci_high"].isna().all() else ov["error_rate"].max()), 1e-9
            )
            valid = ov.dropna(subset=["error_rate"])
            best = valid.iloc[0]["provider"] if len(valid) else None
            rows = []
            for rank, (_, r) in enumerate(ov.iterrows(), start=1):
                p = r["provider"]
                if p == best:
                    verdict = '<span class="verdict best">lowest</span>'
                else:
                    v, _row = findings.paired_verdict(d.paired, tier, metric, "all", "all", best, p)
                    text = {
                        "a_better": f"behind {best} (paired test)",
                        "b_better": f"ahead of {best} (paired test)",
                        "no_difference": "no detectable difference",
                    }.get(v or "", "not tested")
                    verdict = f'<span class="verdict">{_e(text)}</span>'
                er, lo, hi = r["error_rate"], r["ci_low"], r["ci_high"]
                bar = ""
                if not _nan(er):
                    lo_v = float(lo) if not _nan(lo) else float(er)
                    hi_v = float(hi) if not _nan(hi) else float(er)
                    color = ctx.color.get(p, "#888")
                    bar = (
                        f'<span class="track" aria-hidden="true">'
                        f'<span class="whisker" style="left:{lo_v / scale * 100:.2f}%;'
                        f'width:{max(hi_v - lo_v, 0) / scale * 100:.2f}%;background:{color}"></span>'
                        f'<span class="pt" style="left:{float(er) / scale * 100:.2f}%;background:{color}"></span></span>'
                    )
                rows.append(
                    f'<tr class="{"top" if p == best else ""}" data-rank="{rank}" data-provider="{_e(p)}" '
                    f'data-er="{_sortval(er)}" data-cost="{_sortval(r["cost_per_hour"])}" '
                    f'data-lat="{_sortval(r["latency_p50"])}" data-rtf="{_sortval(r["rtf"])}" data-n="{int(r["n_clips"])}">'
                    f'<td class="r num">{rank}</td>'
                    f'<td class="prov"><span class="prov-name">{ctx.dot(p)}{_e(p)}</span>'
                    f'<span class="model mono">{_e(r["model_id"])}</span></td>'
                    f'<td><div class="ci"><span class="val num">{_e(pct(er))}</span>{bar}'
                    f'<span class="range num">{_e(pct(lo))} to {_e(pct(hi))}</span></div></td>'
                    f"<td>{verdict}</td>"
                    f'<td class="r num">{_e(money(r["cost_per_hour"]))}</td>'
                    f'<td class="r num">{_e(secs(r["latency_p50"]))}</td>'
                    f'<td class="r num">{"n/a" if _nan(r["rtf"]) else f"{float(r['rtf']):.3f}"}</td>'
                    f'<td class="r num">{int(r["n_clips"])}</td></tr>'
                )
            head = (
                '<tr><th class="r" data-key="rank" aria-sort="ascending"><button type="button">#</button></th>'
                '<th class="prov" data-key="provider"><button type="button">Provider</button></th>'
                f'<th data-key="er"><button type="button">{_e(metric)} and 95% CI</button></th>'
                "<th>vs lowest</th>"
                '<th class="r" data-key="cost"><button type="button">Cost / audio h</button></th>'
                '<th class="r" data-key="lat"><button type="button">p50 latency</button></th>'
                '<th class="r" data-key="rtf"><button type="button">RTF</button></th>'
                '<th class="r" data-key="n"><button type="button">Clips</button></th></tr>'
            )
            panels.append(
                f'<div data-panel data-tier="{tier}" data-metric="{_e(metric)}">'
                f'<div class="panel-head">{_tier_badge(tier)}<span>metric <b class="mono">{_e(metric)}</b>, '
                f"lower is better</span></div>"
                f'<div class="card scroll"><table class="lb sortable"><thead>{head}</thead>'
                f"<tbody>{''.join(rows)}</tbody></table></div>"
                f'<p class="caption">{_e(TIER_NOTE.get(tier, ""))} "vs lowest" comes from the paired test '
                f"(paired.parquet); everything else from aggregates.parquet, tier={_e(tier)}, "
                f"metric={_e(metric)}, slice=all.</p></div>"
            )
    return (
        '<section id="leaderboard" class="wrap"><h2>Leaderboard</h2>'
        '<p class="lead">All clips, one row per provider. The bar is the 95% confidence interval; click a column '
        "to sort.</p>"
        f"{_controls(ctx, True)}{''.join(panels)}</section>"
    )


def _heat_table(ctx: Ctx, tier: str, metric: str, kind: str) -> str:
    a = ctx.data.aggregates
    sub = a[(a["tier"] == tier) & (a["metric"] == metric) & (a["slice_kind"] == kind)]
    if sub.empty:
        return ""
    if kind == "duration":
        present = set(sub["slice"])
        cols = [s for s in charts.DURATION_ORDER if s in present] + sorted(
            present - set(charts.DURATION_ORDER)
        )
    else:
        cols = sorted(set(sub["slice"]))
    order = ctx.overall(tier, metric)["provider"].tolist()
    providers = order + sorted(set(sub["provider"]) - set(order))
    usable = sub[~sub["too_small"].astype(bool)].dropna(subset=["error_rate"])
    lo = float(usable["error_rate"].min()) if len(usable) else 0.0
    hi = float(usable["error_rate"].max()) if len(usable) else 1.0
    best_in_col = {}
    for c in cols:
        u = usable[usable["slice"] == c].sort_values("error_rate")
        if len(u):
            best_in_col[c] = u.iloc[0]["provider"]
    head = '<tr><th class="prov">Provider</th>' + "".join(f"<th>{_e(c)}</th>" for c in cols) + "</tr>"
    body = []
    for p in providers:
        cells = []
        for c in cols:
            r = sub[(sub["provider"] == p) & (sub["slice"] == c)]
            if r.empty or _nan(r.iloc[0]["error_rate"]):
                cells.append('<td class="cell na">n/a</td>')
                continue
            r = r.iloc[0]
            n = int(r["n_clips"])
            if bool(r["too_small"]):
                cells.append(
                    f'<td class="cell small" title="too small to conclude (n={n})">{_e(pct(r["error_rate"]))}'
                    f'<span class="n">n={n}, too small</span></td>'
                )
                continue
            step = (
                0
                if hi <= lo
                else min(RAMP_STEPS - 1, int((float(r["error_rate"]) - lo) / (hi - lo) * RAMP_STEPS))
            )
            best = " best" if best_in_col.get(c) == p else ""
            cells.append(
                f'<td class="cell h{step}{best}" title="{_e(p)}, {_e(c)}: {_e(pct(r["error_rate"]))}, '
                f'95% CI {_e(pct(r["ci_low"]))} to {_e(pct(r["ci_high"]))}, n={n}">'
                f'{_e(pct(r["error_rate"]))}<span class="n">n={n}</span></td>'
            )
        body.append(
            f'<tr><td class="prov"><span class="prov-name">{ctx.dot(p)}{_e(p)}</span></td>{"".join(cells)}</tr>'
        )
    return f'<div class="card scroll"><table class="hm"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'


def heatmaps(ctx: Ctx) -> str:
    panels = []
    for tier in ctx.tiers:
        for metric in ctx.metrics:
            tag = _heat_table(ctx, tier, metric, "tag")
            dur = _heat_table(ctx, tier, metric, "duration")
            if not (tag or dur):
                continue
            inner = ""
            if tag:
                inner += f'<h3 style="font-size:14px;margin:0 0 8px">By tag</h3>{tag}'
            if dur:
                inner += f'<h3 style="font-size:14px;margin:18px 0 8px">By clip duration</h3>{dur}'
            panels.append(
                f'<div data-panel data-tier="{tier}" data-metric="{_e(metric)}">'
                f'<div class="panel-head">{_tier_badge(tier)}<span>metric <b class="mono">{_e(metric)}</b></span></div>'
                f"{inner}"
                f'<p class="caption">source: aggregates.parquet, tier={_e(tier)}, metric={_e(metric)}, '
                f"slice_kind=tag and duration.</p></div>"
            )
    if not panels:
        return ""
    ramp = "".join(f'<i style="background:var(--h{i})"></i>' for i in range(RAMP_STEPS))
    legend = (
        f'<div class="legend"><span>lower error <span class="ramp">{ramp}</span> higher error</span>'
        '<span><span class="sw" style="background:var(--hatch);border:1px solid var(--border)"></span>'
        "too small to conclude</span><span>outlined: lowest in that column</span></div>"
    )
    return (
        '<section id="by-clip" class="wrap"><h2>Who wins on which kind of clip</h2>'
        '<p class="lead">Error rate per provider and clip type. Hatched cells have too few clips to conclude '
        "anything; the outline marks the lowest point estimate, not a tested win.</p>"
        f"{_controls(ctx, True)}{''.join(panels)}{legend}</section>"
    )


def chart_section(ctx: Ctx) -> tuple[str, dict]:
    specs: dict[str, dict] = {}
    panels = []
    for tier in ctx.tiers:
        figs = []
        for b in ctx.charts:
            if not b.name.endswith(f"_{tier}"):
                continue
            base = b.name[: -len(tier) - 1]
            title, cls = CHART_TITLES.get(base, (b.text, ""))
            # The card carries the title; drop the spec's own title so it is not shown twice.
            specs[b.name] = {k: v for k, v in (b.spec or {}).items() if k != "title"}
            figs.append(
                f'<figure class="card {cls}"><h3>{_e(title)}</h3><div class="chart" id="chart-{_e(b.name)}">'
                f'<img src="charts/{_e(b.name)}.png" alt="{_e(title)}, {_e(tier)} tier" loading="lazy"></div>'
                f"<figcaption>{_e(b.caption)}</figcaption></figure>"
            )
        if figs:
            panels.append(
                f'<div data-panel data-tier="{tier}"><div class="panel-head">{_tier_badge(tier)}'
                f'<span>metric <b class="mono">{_e(ctx.data.metric)}</b></span></div>'
                f'<div class="charts">{"".join(figs)}</div></div>'
            )
    if not panels:
        return "", specs
    html_ = (
        '<section id="charts" class="wrap"><h2>Charts</h2>'
        '<p class="lead">Accuracy against cost, by clip type, hallucination and clip length. Hover a mark for '
        "values.</p>"
        f"{_controls(ctx, False)}{''.join(panels)}</section>"
    )
    return html_, specs


def findings_section(ctx: Ctx) -> str:
    d = ctx.data
    panels = []
    for tier in ctx.tiers:
        lines = findings.draft_findings(d.aggregates, d.paired, d.stability, tier, d.metric)
        if not lines:
            continue
        items = []
        for line in lines:
            text, src = _split_source(line)
            items.append(f'<li><p class="text">{_draft(text)}</p><p class="src mono">({_e(src)})</p></li>')
        panels.append(
            f'<div data-panel data-tier="{tier}"><div class="panel-head">{_tier_badge(tier)}</div>'
            f'<ol class="findings">{"".join(items)}</ol></div>'
        )
    return (
        '<section id="findings" class="wrap"><h2>Findings</h2>'
        '<p class="note"><b>Drafted from the tables, not yet checked.</b> Every line starts with "DRAFT: verify." '
        "and names its source table. Only a paired test verdict can say one provider beats another.</p>"
        f"{_controls(ctx, False)}{''.join(panels)}</section>"
    )


def errors_section(ctx: Ctx) -> str:
    errors = ctx.data.errors
    if not errors:
        return (
            '<section id="errors" class="wrap"><h2>What breaks each model</h2>'
            '<p class="lead">No errors.json for this run. Run <code>clipbench errors</code>.</p></section>'
        )
    cards = []
    for pid, info in sorted(errors.get("providers", {}).items()):
        subs = info.get("top_substitutions", [])[:8]
        chips = (
            "".join(
                f'<span class="sub">{_e(r)}<span class="arrow">&rarr;</span>{_e(h)}<b>x{int(n)}</b></span>'
                for r, h, n in subs
            )
            or '<span class="sub">none</span>'
        )
        cards.append(
            f'<div class="card err-card"><h3>{ctx.dot(pid)}{_e(pid)}</h3><div class="subs">{chips}</div></div>'
        )
    ex_rows = "".join(
        f'<tr><td>{ctx.dot(e["provider"])} {_e(e["provider"])}</td><td class="mono">{_e(e["video_id"])}</td>'
        f'<td class="wrapcell">{_e(e["ref_snippet"])}</td><td class="wrapcell">{_e(e["hyp_snippet"])}</td></tr>'
        for e in errors.get("examples", [])
    )
    examples = ""
    if ex_rows:
        examples = (
            '<div class="card scroll"><table class="examples"><thead><tr><th>Provider</th><th>Clip</th>'
            "<th>Reference (snippet)</th><th>Output (snippet)</th></tr></thead>"
            f"<tbody>{ex_rows}</tbody></table></div>"
            '<p class="caption">Short context around one error, never a full caption. source: errors.json</p>'
        )
    n_fail = len(errors.get("all_fail", []))
    thr = errors.get("all_fail_threshold", 0.5)
    return (
        '<section id="errors" class="wrap"><h2>What breaks each model</h2>'
        '<p class="lead">Most common substitutions (reference &rarr; output) on normalised tokens of the primary '
        "metric. Japanese and Korean characters are grouped into spans.</p>"
        f'<div class="err-grid">{"".join(cards)}</div>{examples}'
        f'<p class="caption">{n_fail} {"clip" if n_fail == 1 else "clips"} had every provider above '
        f"{_e(pct(thr))} error. Those usually mean a bad reference and are queued for re-review, not counted as "
        "model failures. source: errors.json</p></section>"
    )


def limits_section(ctx: Ctx) -> str:
    items = "".join(f"<li>{_e(i)}</li>" for i in limitations(ctx.data).items)
    url = methodology_url()
    method = (
        f'<a href="{_e(url)}">{_e(url)}</a>'
        if url
        else f"<code>{_e(METHODOLOGY_DOC)}</code> in the clipbench repository"
    )
    meta = []
    for item in meta_block(ctx.data).items:
        k, _, v = item.partition(": ")
        meta.append(f'<dt>{_e(k)}</dt><dd class="mono">{_e(v)}</dd>')
    return (
        '<section id="limits" class="wrap"><h2>Limitations</h2>'
        f'<div class="card"><ul class="limits">{items}</ul></div></section>'
        '<section id="run" class="wrap"><h2>Methodology and run</h2>'
        f'<p class="lead">How references, normalisation, metrics and statistics work: {method}.</p>'
        f'<div class="card"><dl class="meta">{"".join(meta)}</dl></div></section>'
    )


# ---------------------------------------------------------------- page


def render_html(data: RunData, blocks: list[Block]) -> str:
    ctx = Ctx(data, blocks)
    charts_html, specs = chart_section(ctx)
    body = "".join(
        [
            hero(ctx),
            leaderboard(ctx),
            heatmaps(ctx),
            charts_html,
            findings_section(ctx),
            errors_section(ctx),
            limits_section(ctx),
        ]
    )
    toc = [("leaderboard", "Leaderboard"), ("by-clip", "By clip type"), ("charts", "Charts"),
           ("findings", "Findings"), ("errors", "Errors"), ("limits", "Limitations"), ("run", "Run")]  # fmt: skip
    nav = "".join(f'<a href="#{sid}">{_e(t)}</a>' for sid, t in toc if f'id="{sid}"' in body)
    # Keep the inline JSON safe inside <script>: escape "</" so no spec string can close the tag.
    spec_json = json.dumps(specs, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    desc = f"Speech-to-text models on real {ctx.lang} short-form video: accuracy, cost, failures."
    return f"""<!doctype html>
<html lang="en" class="no-js" data-default-tier="{_e(ctx.head)}" data-default-metric="{_e(data.metric)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clipbench report</title>
<meta name="description" content="{_e(desc)}">
<style>{CSS}</style>
</head>
<body>
<div class="topbar"><div class="wrap">
<a class="brand" href="#top">clipbench <span>{_e(data.run_id)}</span></a>
<nav class="toc" aria-label="Sections">{nav}</nav>
<button id="theme" type="button">Dark</button>
</div></div>
<main>
{body}
</main>
<footer class="wrap">Generated by clipbench. No audio and no full captions are published. Every finding is a draft until a person checks it.</footer>
<script id="chart-specs" type="application/json">{spec_json}</script>
<script src="{VEGA}"></script>
<script src="{VEGA_LITE}"></script>
<script src="{VEGA_EMBED}"></script>
<script>{JS}</script>
</body>
</html>
"""
