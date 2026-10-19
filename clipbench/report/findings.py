"""Draft findings, written from the numbers.

Every sentence starts with "DRAFT: verify." and ends with the exact table it came from, so a human
checks each one before publishing. Only a paired test verdict (paired.parquet a_better / b_better)
may produce the word "beats"; anything else is phrased as "no detectable difference".
"""

from __future__ import annotations

import math

import pandas as pd

PREFIX = "DRAFT: verify."


def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f}%"


def _cost(x: float) -> str:
    return "no API cost (local or free)" if float(x) == 0 else f"${float(x):.2f} per audio hour"


def _src(table: str, **kv) -> str:
    return "(source: " + ", ".join([table] + [f"{k}={v}" for k, v in kv.items()]) + ")"


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def paired_verdict(
    paired: pd.DataFrame | None, tier: str, metric: str, slice_kind: str, slice_: str, a: str, b: str
) -> tuple[str | None, pd.Series | None]:
    """Verdict oriented as 'a_better' / 'b_better' / 'no_difference' for providers a and b, or None."""
    if paired is None or paired.empty:
        return None, None
    m = paired[
        (paired["tier"] == tier) & (paired["metric"] == metric)
        & (paired["slice_kind"] == slice_kind) & (paired["slice"] == slice_)
    ]  # fmt: skip
    hit = m[(m["provider_a"] == a) & (m["provider_b"] == b)]
    if len(hit):
        row = hit.iloc[0]
        return str(row["verdict"]), row
    hit = m[(m["provider_a"] == b) & (m["provider_b"] == a)]
    if len(hit):
        row = hit.iloc[0]
        flip = {"a_better": "b_better", "b_better": "a_better"}
        return flip.get(str(row["verdict"]), str(row["verdict"])), row
    return None, None


def compare_sentence(paired, tier, metric, slice_kind, slice_, a, b, where: str) -> str:
    verdict, row = paired_verdict(paired, tier, metric, slice_kind, slice_, a, b)
    src = _src("paired.parquet", tier=tier, metric=metric, slice=f"{slice_kind}:{slice_}", pair=f"{a}|{b}")
    if verdict is None:
        return f"{PREFIX} {a} vs {b} {where}: no paired test available, so no difference is claimed {src}."
    stats = ""
    if row is not None:
        p = row.get("p_value")
        diff = row.get("diff")
        if _ok(diff):
            stats = f" (paired difference {abs(float(diff)) * 100:.1f} points"
            stats += f", p={float(p):.3g})" if _ok(p) else ")"
    if verdict == "a_better":
        return f"{PREFIX} {a} beats {b} on {metric} {where}{stats} {src}."
    if verdict == "b_better":
        return f"{PREFIX} {b} beats {a} on {metric} {where}{stats} {src}."
    return f"{PREFIX} No detectable difference between {a} and {b} on {metric} {where}{stats} {src}."


def draft_findings(
    aggregates: pd.DataFrame,
    paired: pd.DataFrame | None,
    stability: pd.DataFrame | None,
    tier: str,
    metric: str,
) -> list[str]:
    out: list[str] = []
    agg = aggregates[(aggregates["tier"] == tier) & (aggregates["metric"] == metric)]
    overall = agg[agg["slice_kind"] == "all"].dropna(subset=["error_rate"]).sort_values("error_rate")
    if overall.empty:
        return out
    src_all = _src("aggregates.parquet", tier=tier, metric=metric, slice="all")

    # Best overall, then paired comparison against every other provider.
    best = overall.iloc[0]
    out.append(
        f"{PREFIX} {best['provider']} ({best['model_id']}) has the lowest {metric} on the {tier} tier: "
        f"{_pct(best['error_rate'])} (95% CI {_pct(best['ci_low'])} to {_pct(best['ci_high'])}) "
        f"over {int(best['n_clips'])} clips {src_all}."
    )
    for _, other in overall.iloc[1:].iterrows():
        out.append(
            compare_sentence(
                paired, tier, metric, "all", "all", best["provider"], other["provider"], "overall"
            )
        )

    # Best per tag slice that is not too small.
    tags = agg[agg["slice_kind"] == "tag"]
    small_slices = []
    for slice_, grp in tags.groupby("slice", sort=True):
        usable = grp[~grp["too_small"].astype(bool)].dropna(subset=["error_rate"]).sort_values("error_rate")
        if usable.empty:
            small_slices.append(str(slice_))
            continue
        top = usable.iloc[0]
        src = _src("aggregates.parquet", tier=tier, metric=metric, slice=f"tag:{slice_}")
        out.append(
            f"{PREFIX} On clips tagged {slice_}, {top['provider']} has the lowest {metric}: "
            f"{_pct(top['error_rate'])} over {int(top['n_clips'])} clips {src}."
        )
        if len(usable) > 1:
            out.append(
                compare_sentence(
                    paired,
                    tier,
                    metric,
                    "tag",
                    slice_,
                    top["provider"],
                    usable.iloc[1]["provider"],
                    f"on clips tagged {slice_}",
                )
            )
    if small_slices:
        src = _src("aggregates.parquet", tier=tier, metric=metric, slice_kind="tag", too_small=True)
        out.append(f"{PREFIX} Too small to conclude anything: tag slices {', '.join(small_slices)} {src}.")

    # Cheapest provider whose error rate falls inside the best provider's 95% CI.
    within = overall[overall["error_rate"] <= best["ci_high"]].copy()
    within["cost"] = pd.to_numeric(within["cost_per_hour"], errors="coerce")
    within = within.dropna(subset=["cost"]).sort_values(["cost", "error_rate"])
    if len(within):
        cheap = within.iloc[0]
        if cheap["provider"] == best["provider"]:
            out.append(
                f"{PREFIX} No provider is cheaper than {best['provider']} while staying inside its 95% CI; "
                f"it costs {_cost(cheap['cost'])} {src_all}."
            )
        else:
            out.append(
                f"{PREFIX} {cheap['provider']} is the cheapest provider whose {metric} "
                f"({_pct(cheap['error_rate'])}) falls inside the 95% CI of the best, at {_cost(cheap['cost'])}; "
                f"overlapping a CI is not a paired test {src_all}."
            )

    # Hallucination.
    hal = overall.dropna(subset=["hallucination_rate"]).sort_values("hallucination_rate", ascending=False)
    if len(hal):
        h = hal.iloc[0]
        out.append(
            f"{PREFIX} {h['provider']} has the highest hallucination rate: {_pct(h['hallucination_rate'])} of "
            f"reference tokens, long output on {_pct(h.get('long_output_rate'))} of clips {src_all}."
        )

    # Latency.
    lat = overall.dropna(subset=["latency_p50"]).sort_values("latency_p50")
    if len(lat):
        q = lat.iloc[0]
        rtf = f", real time factor {float(q['rtf']):.3f}" if _ok(q.get("rtf")) else ""
        out.append(
            f"{PREFIX} {q['provider']} has the lowest median latency: {float(q['latency_p50']) / 1000:.2f} s per clip"
            f"{rtf} {src_all}."
        )

    # Stability across repeats.
    if stability is not None and not stability.empty and "text_disagreement" in stability:
        st = stability.groupby("provider")["text_disagreement"].mean().dropna().sort_values()
        st = st[st.index.isin(set(overall["provider"]))]
        if len(st):
            src = _src("stability.parquet", metric="text_disagreement", agg="mean over repeated clips")
            if len(st) == 1:
                out.append(
                    f"{PREFIX} {st.index[0]} disagrees with itself on {_pct(float(st.iloc[0]))} of tokens "
                    f"between repeated runs {src}."
                )
            else:
                out.append(
                    f"{PREFIX} Most stable across repeats: {st.index[0]} ({_pct(float(st.iloc[0]))} token "
                    f"disagreement); least stable: {st.index[-1]} ({_pct(float(st.iloc[-1]))}) {src}."
                )
    return out
