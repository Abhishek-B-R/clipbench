"""Altair charts for the report. Each builder takes data frames for ONE tier and returns a chart.

Specs are inlined in site/index.html (rendered by vega-embed) and exported to PNG with vl-convert for
report.md. Provider colours follow a fixed order (sorted provider ids), never rank.
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd

# Reference categorical palette (dataviz skill), light steps. Fixed order, never cycled.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
GREY = "#b5b4ae"
TOO_SMALL_LABEL = "too small to conclude"
ZERO_COST_LABEL = "zero cost (plotted at floor)"
WIDTH = 560


def provider_scale(providers: list[str]) -> alt.Scale:
    providers = sorted(providers)
    colours = [PALETTE[i % len(PALETTE)] for i in range(len(providers))]
    return alt.Scale(domain=providers, range=colours)


def _base_config(chart: alt.Chart) -> alt.Chart:
    return (
        chart.configure(background="transparent", font="system-ui, -apple-system, Segoe UI, sans-serif")
        .configure_axis(
            gridColor="#8884", domainColor="#8888", tickColor="#8888", labelColor="#888", titleColor="#888"
        )
        .configure_legend(labelColor="#888", titleColor="#888")
        .configure_title(color="#888", anchor="start", fontSize=14)
        .configure_view(stroke=None)
    )


def accuracy_vs_cost(agg_all: pd.DataFrame, metric: str, title: str) -> alt.Chart:
    """agg_all: aggregates rows with slice_kind == 'all' for one tier and metric."""
    df = agg_all[
        ["provider", "model_id", "error_rate", "ci_low", "ci_high", "cost_per_hour", "n_clips"]
    ].copy()
    cost = pd.to_numeric(df["cost_per_hour"], errors="coerce").fillna(0.0)
    positive = cost[cost > 0]
    floor = float(positive.min()) / 3 if len(positive) else 0.01
    df["cost_plot"] = cost.where(cost > 0, floor)
    df["pricing"] = ["paid" if c > 0 else ZERO_COST_LABEL for c in cost]
    df["cost_label"] = [f"${c:.2f}/h" if c > 0 else "$0/h" for c in cost]
    scale = provider_scale(list(df["provider"]))
    x = alt.X("cost_plot:Q", scale=alt.Scale(type="log"), title="cost per audio hour (USD, log scale)")
    base = alt.Chart(df)
    bars = base.mark_rule(strokeWidth=2).encode(
        x=x,
        y=alt.Y("ci_low:Q", title=f"{metric} (lower is better)", axis=alt.Axis(format=".0%")),
        y2="ci_high:Q",
        color=alt.Color("provider:N", scale=scale, legend=alt.Legend(title="provider")),
    )
    points = base.mark_point(size=110, filled=True, stroke="white", strokeWidth=2).encode(
        x=x,
        y="error_rate:Q",
        color=alt.Color("provider:N", scale=scale),
        shape=alt.Shape("pricing:N", legend=alt.Legend(title="pricing")),
        tooltip=[
            "provider",
            "model_id",
            alt.Tooltip("error_rate:Q", format=".3f"),
            alt.Tooltip("ci_low:Q", format=".3f"),
            alt.Tooltip("ci_high:Q", format=".3f"),
            "cost_label",
            "n_clips",
        ],
    )
    df["label"] = [p if c > 0 else f"{p} ($0/h)" for p, c in zip(df["provider"], cost, strict=False)]
    labels = (
        alt.Chart(df)
        .mark_text(align="left", dx=8, dy=-8, color="#888")
        .encode(x=x, y="error_rate:Q", text="label")
    )
    return _base_config((bars + points + labels).properties(width=WIDTH, height=320, title=title))


def by_slice(agg: pd.DataFrame, metric: str, title: str, slice_kind: str = "tag") -> alt.Chart:
    """Error rate per slice and provider. Too small slices are grey and labelled."""
    df = agg[agg["slice_kind"] == slice_kind][
        ["provider", "slice", "n_clips", "error_rate", "ci_low", "ci_high", "too_small"]
    ].copy()
    df["too_small"] = df["too_small"].astype(bool)
    small = df.groupby("slice")["too_small"].all()
    nmax = df.groupby("slice")["n_clips"].max()
    df["slice_label"] = [
        f"{s} (n={int(nmax[s])}, {TOO_SMALL_LABEL})" if small[s] else f"{s} (n={int(nmax[s])})"
        for s in df["slice"]
    ]
    df["status"] = [TOO_SMALL_LABEL if t else "ok" for t in df["too_small"]]
    scale = provider_scale(list(df["provider"].unique()))
    color = alt.condition(alt.datum.too_small, alt.value(GREY), alt.Color("provider:N", scale=scale))
    y = alt.Y("slice_label:N", title=None, sort=sorted(df["slice_label"].unique()))
    bars = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4, height=10)
        .encode(
            x=alt.X(
                "error_rate:Q", title=f"{metric} (lower is better)", axis=alt.Axis(format=".0%", tickCount=6)
            ),
            y=y,
            yOffset=alt.YOffset("provider:N"),
            color=color,
            opacity=alt.condition(alt.datum.too_small, alt.value(0.6), alt.value(1.0)),
            tooltip=[
                "provider",
                "slice",
                "n_clips",
                alt.Tooltip("error_rate:Q", format=".3f"),
                alt.Tooltip("ci_low:Q", format=".3f"),
                alt.Tooltip("ci_high:Q", format=".3f"),
                "status",
            ],
        )
    )
    legend_layer = (
        alt.Chart(df)
        .mark_point(opacity=0)
        .encode(color=alt.Color("provider:N", scale=scale, legend=alt.Legend(title="provider")))
    )
    height = max(120, 18 * len(df))
    return _base_config((bars + legend_layer).properties(width=WIDTH - 120, height=height, title=title))


def hallucination(agg_all: pd.DataFrame, title: str) -> alt.Chart:
    df = agg_all[["provider", "hallucination_rate", "long_output_rate", "n_clips"]].copy()
    long = df.melt(
        id_vars=["provider", "n_clips"],
        value_vars=["hallucination_rate", "long_output_rate"],
        var_name="measure",
        value_name="rate",
    )
    long["measure"] = long["measure"].map(
        {
            "hallucination_rate": "hallucinated tokens / ref tokens",
            "long_output_rate": "share of clips with long output",
        }
    )
    scale = provider_scale(list(df["provider"]))
    chart = (
        alt.Chart(long)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("rate:Q", title="rate", axis=alt.Axis(format=".0%", tickCount=6)),
            y=alt.Y("provider:N", title=None, sort="-x"),
            color=alt.Color("provider:N", scale=scale, legend=None),
            row=alt.Row(
                "measure:N", title=None, header=alt.Header(labelColor="#888", labelAngle=0, labelAlign="left")
            ),
            tooltip=["provider", "measure", alt.Tooltip("rate:Q", format=".3f"), "n_clips"],
        )
        .properties(width=WIDTH - 140, height=max(60, 24 * len(df)))
    )
    return _base_config(chart.properties(title=title))


DURATION_ORDER = ["<15s", "15-30s", "30-60s", "60-90s"]


def by_duration(agg: pd.DataFrame, metric: str, title: str) -> alt.Chart:
    df = agg[agg["slice_kind"] == "duration"][
        ["provider", "slice", "n_clips", "error_rate", "too_small"]
    ].copy()
    df["too_small"] = df["too_small"].astype(bool)
    df["status"] = [TOO_SMALL_LABEL if t else "ok" for t in df["too_small"]]
    order = [s for s in DURATION_ORDER if s in set(df["slice"])] + sorted(
        set(df["slice"]) - set(DURATION_ORDER)
    )
    scale = provider_scale(list(df["provider"].unique()))
    x = alt.X("slice:N", sort=order, title="clip duration", axis=alt.Axis(labelAngle=0))
    line = (
        alt.Chart(df)
        .mark_line(strokeWidth=2)
        .encode(
            x=x,
            y=alt.Y("error_rate:Q", title=f"{metric} (lower is better)", axis=alt.Axis(format=".0%")),
            color=alt.Color("provider:N", scale=scale, legend=alt.Legend(title="provider")),
        )
    )
    pts = (
        alt.Chart(df)
        .mark_point(size=80, filled=True)
        .encode(
            x=x,
            y="error_rate:Q",
            color=alt.condition(alt.datum.too_small, alt.value(GREY), alt.Color("provider:N", scale=scale)),
            shape=alt.Shape("status:N", legend=alt.Legend(title="slice size")),
            tooltip=["provider", "slice", "n_clips", alt.Tooltip("error_rate:Q", format=".3f"), "status"],
        )
    )
    return _base_config((line + pts).properties(width=WIDTH, height=280, title=title))


def to_spec(chart: alt.Chart) -> dict:
    return chart.to_dict()


def export_png(spec: dict, paths: list[Path], scale: float = 2.0) -> None:
    """Render a Vega-Lite spec to PNG with vl-convert (white background for markdown readers)."""
    import vl_convert as vlc

    spec = json.loads(json.dumps(spec))
    spec.setdefault("config", {})["background"] = "white"
    png = vlc.vegalite_to_png(vl_spec=spec, scale=scale)
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)
