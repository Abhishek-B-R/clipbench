"""Draft findings, written from the numbers.

Every sentence starts with "DRAFT: verify." and ends with the exact table it came from, so a human
checks each one before publishing. Only a paired test verdict (paired.parquet a_better / b_better)
may produce the word "beats"; anything else is phrased as "no detectable difference".

Callers pass `eligible` (clipbench.report.rules): providers below the coverage minimum are dropped from
the aggregates and the paired tests before anything is ranked, corrected or written, so an excluded
provider never appears in a finding. A "beats" whose channel-clustered interval crosses zero is flagged
cluster_sensitive and its sentence says so.
"""

from __future__ import annotations

import math

import pandas as pd

from clipbench.report import rules

PREFIX = "DRAFT: verify."


def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f}%"


def _cost(x: float) -> str:
    return "no API cost (local or free)" if float(x) == 0 else f"${float(x):.2f} per audio hour"


def _src(table: str, **kv) -> str:
    return "(source: " + ", ".join([table] + [f"{k}={v}" for k, v in kv.items()]) + ")"


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


FDR_Q = 0.05


def adjust_paired(paired: pd.DataFrame | None, q: float = FDR_Q) -> pd.DataFrame | None:
    """Benjamini-Hochberg over every paired test of a tier in the run.

    A run makes many paired comparisons (each pair of models on each slice and metric), so at alpha 0.05
    some "beats" would be false positives. A verdict survives only when it also clears the false discovery
    rate `q` within its tier; otherwise it becomes no_difference. Adds p_adjusted and keeps the raw verdict
    as verdict_raw.
    """
    if paired is None or paired.empty or "p_value" not in paired.columns:
        return paired
    if "verdict_raw" in paired.columns:
        return paired  # already adjusted
    out = paired.copy()
    out["verdict_raw"] = out["verdict"]
    out["p_adjusted"] = float("nan")
    for _, idx in out.groupby("tier").groups.items():
        pv = pd.to_numeric(out.loc[idx, "p_value"], errors="coerce")
        ok = pv.dropna().sort_values()
        m = len(ok)
        if not m:
            continue
        ranks = pd.Series(range(1, m + 1), index=ok.index, dtype=float)
        adj = (ok * m / ranks).iloc[::-1].cummin().iloc[::-1].clip(upper=1.0)
        out.loc[adj.index, "p_adjusted"] = adj
    survives = out["p_adjusted"] <= q
    out.loc[~survives & out["verdict"].isin(["a_better", "b_better"]), "verdict"] = "no_difference"
    return out


def restrict(
    aggregates: pd.DataFrame,
    paired: pd.DataFrame | None,
    stability: pd.DataFrame | None,
    eligible: set[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    """Eligible providers only, then Benjamini-Hochberg over the remaining paired tests. eligible=None keeps
    everything (direct calls in tests and tools)."""
    if eligible is None:
        return aggregates, adjust_paired(paired), stability
    agg = rules.eligible_aggregates(aggregates, eligible)
    pr = adjust_paired(rules.eligible_paired(paired, eligible))
    if stability is not None and not stability.empty and "provider" in stability:
        stability = stability[stability["provider"].astype(str).isin(eligible)]
    return agg, pr, stability


def cluster_sensitive(row) -> tuple[bool, float | None, float | None]:
    """(sensitive, low, high): the channel-clustered interval of a paired row crosses zero. Unknown (no
    channel information) counts as not sensitive, with no bounds."""
    if row is None:
        return False, None, None
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    lo, hi = _f(get("ci_low_cluster")), _f(get("ci_high_cluster"))
    if lo is None or hi is None:
        return False, None, None
    return bool(lo <= 0 <= hi), lo, hi


def _cluster_note(row_verdict: str, lo: float, hi: float) -> str:
    """Sentence for a cluster sensitive "beats". lo and hi bound rate_a - rate_b of the stored row; they are
    turned into the winner's advantage in points (positive = fewer errors for the winner)."""
    adv_lo, adv_hi = (-hi, -lo) if row_verdict == "a_better" else (lo, hi)
    return (
        f" Sensitive to channel clustering: resampling whole channels puts the advantage between "
        f"{adv_lo * 100:.1f} and {adv_hi * 100:.1f} points, an interval that includes zero."
    )


def paired_verdict(
    paired: pd.DataFrame | None, tier: str, metric: str, slice_kind: str, slice_: str, a: str, b: str
) -> tuple[str | None, pd.Series | None]:
    """Verdict oriented as 'a_better' / 'b_better' / 'no_difference' for providers a and b, or None."""
    if paired is None or paired.empty:
        return None, None
    paired = adjust_paired(paired)
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
    sens, lo, hi = cluster_sensitive(row) if verdict in ("a_better", "b_better") else (False, None, None)
    note = "." + _cluster_note(str(row.get("verdict")), lo, hi).rstrip(".") if sens else ""
    if verdict == "a_better":
        return f"{PREFIX} {a} beats {b} on {metric} {where}{stats}{note} {src}."
    if verdict == "b_better":
        return f"{PREFIX} {b} beats {a} on {metric} {where}{stats}{note} {src}."
    return f"{PREFIX} No detectable difference between {a} and {b} on {metric} {where}{stats} {src}."


def draft_findings(
    aggregates: pd.DataFrame,
    paired: pd.DataFrame | None,
    stability: pd.DataFrame | None,
    tier: str,
    metric: str,
    eligible: set[str] | None = None,
) -> list[str]:
    out: list[str] = []
    aggregates, paired, stability = restrict(aggregates, paired, stability, eligible)
    agg = aggregates[(aggregates["tier"] == tier) & (aggregates["metric"] == metric)]
    overall = rules.rank_order(agg[agg["slice_kind"] == "all"])
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
        usable = rules.rank_order(grp[~grp["too_small"].astype(bool)])
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
                + (
                    f"it has no API cost (local or free) {src_all}."
                    if float(cheap["cost"]) == 0
                    else f"it costs {_cost(cheap['cost'])} {src_all}."
                )
            )
        else:
            out.append(
                f"{PREFIX} {cheap['provider']} is the cheapest provider whose {metric} "
                f"({_pct(cheap['error_rate'])}) falls inside the 95% CI of the best, at {_cost(cheap['cost'])}; "
                f"overlapping a CI is not a paired test {src_all}."
            )

    # Hallucination.
    hal = overall.dropna(subset=["hallucination_rate"]).sort_values("hallucination_rate", ascending=False)
    if (
        len(hal)
        and float(hal.iloc[0]["hallucination_rate"]) == 0
        and not float(pd.to_numeric(hal["long_output_rate"], errors="coerce").fillna(0).max())
    ):
        out.append(
            f"{PREFIX} No provider hallucinated: 0.0% of reference tokens and no long outputs {src_all}."
        )
    elif len(hal):
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


# ---------------------------------------------------------------- v2: structured findings per language
#
# Rules (METHODOLOGY.md, "When a language is published"):
# - A sentence may compare two models only when paired.parquet says a_better or b_better; then it says
#   "beats". "No detectable difference" results are not findings (nothing to share).
# - Single-model facts (timing, hallucination over non-speech, caption readiness, code-switch, music vs no
#   music) are allowed when the slice behind them has at least rules.MIN_SLICE_CLIPS clips.
# - A null metric (for example no word timings) produces no finding; it is never reported as 0.
# - The DRAFT marker lives in `status`, not in `text`, so the site can badge it.

LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "ko": "Korean", "es": "Spanish", "hi": "Hindi", "fr": "French"}
SLICE_WORDS = {
    "all": "all clips",
    "music": "clips with music under the speech",
    "no_music": "clips without music",
}


def _slug(*parts: str) -> str:
    import re

    s = "-".join(str(p) for p in parts if p not in (None, ""))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:90]


def _fid(slug: str) -> str:
    import hashlib

    return hashlib.sha1(slug.encode()).hexdigest()[:10]


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _slice_words(kind: str, slice_: str) -> str:
    if kind == "all":
        return "all clips"
    if kind == "audio_condition":
        return SLICE_WORDS.get(slice_, slice_)
    if kind == "category":
        return f"{slice_.replace('_', ' ')} clips"
    if kind == "tag":
        return f"clips tagged {slice_.replace('_', ' ')}"
    return f"{slice_} clips"


# Which column the web table sorts by when a finding links to it (web contract: timing, offset, halluc,
# punct, casing, codeswitch).
SORT_BY_KIND = {
    "timing": "timing",
    "hallucination": "halluc",
    "readiness": "punct",
    "codeswitch": "codeswitch",
}


def _finding(
    kind: str,
    slug: str,
    tier: str,
    text: str,
    table: str,
    filt: dict,
    numbers: dict,
    provider: str | None = None,
    sensitive: bool = False,
) -> dict:
    """One finding in the web contract shape (docs/V2.md, findings/<lang>.json). `kind` and `tier` are kept for
    the report and OG images; the exporter writes only the contract keys."""
    filters = dict(filt)
    if provider is not None:
        filters["provider"] = provider
    if kind in SORT_BY_KIND:
        filters["sort"] = SORT_BY_KIND[kind]
    return {
        "id": _fid(slug),
        "slug": slug,
        "kind": kind,
        "status": "draft",
        "tier": tier,
        "text": text,
        "source": {"table": table.removesuffix(".parquet"), "filters": filters},
        "numbers": {k: v for k, v in numbers.items() if v is not None},
        "cluster_sensitive": bool(sensitive),
    }


def structured_findings(
    aggregates: pd.DataFrame,
    paired: pd.DataFrame | None,
    metric: str,
    tier: str = "verified",
    min_clips: int = 15,
    eligible: set[str] | None = None,
) -> list[dict]:
    """Findings for one language's published run. The caller decides publication; this applies the
    eligible-provider, paired-test and too-small rules."""
    out: list[dict] = []
    aggregates, paired, _ = restrict(aggregates, paired, None, eligible)
    agg = aggregates[(aggregates["tier"] == tier) & (aggregates["metric"] == metric)]
    if agg.empty:
        return out

    # 1. Model comparisons that survive the paired test, overall and per category / audio condition.
    if paired is not None and not paired.empty:
        p = paired[(paired["tier"] == tier) & (paired["metric"] == metric)]
        p = p[p["verdict"].isin(["a_better", "b_better"]) & (p["n_clips"] >= min_clips)]
        p = p[p["slice_kind"].isin(["all", "category", "audio_condition", "tag"])]
        for r in p.itertuples(index=False):
            win, lose = (
                (r.provider_a, r.provider_b) if r.verdict == "a_better" else (r.provider_b, r.provider_a)
            )
            ea = agg[(agg["slice_kind"] == r.slice_kind) & (agg["slice"] == r.slice)]
            er = {str(x.provider): _f(x.error_rate) for x in ea.itertuples(index=False)}
            where = _slice_words(r.slice_kind, r.slice)
            diff = abs(_f(r.diff) or 0.0)
            # Quote only numbers measured on the clips both models scored: the paired difference and its
            # interval. Each model's slice-wide rate can cover different clips, so it stays out of the claim.
            lo, hi = sorted((abs(_f(r.ci_low) or 0.0), abs(_f(r.ci_high) or 0.0)))
            p_adj = _f(getattr(r, "p_adjusted", None))
            p_txt = f"adjusted p={p_adj:.3g}" if p_adj is not None else f"p={float(r.p_value):.3g}"
            text = (
                f"{win} beats {lose} on {metric} on {where}: {diff * 100:.1f} points fewer errors "
                f"(95% CI {lo * 100:.1f} to {hi * 100:.1f}) on the {int(r.n_clips)} clips both scored ({p_txt})."
            )
            sens, c_lo, c_hi = cluster_sensitive(r)
            if sens:
                text += _cluster_note(str(r.verdict), c_lo, c_hi)
            slug = _slug(
                metric,
                win,
                "beats",
                lose,
                r.slice_kind if r.slice_kind != "all" else "",
                r.slice if r.slice != "all" else "",
            )
            out.append(
                _finding(
                    "comparison",
                    slug,
                    tier,
                    text,
                    "paired.parquet",
                    {
                        "tier": tier,
                        "metric": metric,
                        "slice_kind": r.slice_kind,
                        "slice": r.slice,
                        "provider_a": r.provider_a,
                        "provider_b": r.provider_b,
                    },
                    {
                        "winner_error_rate_slice": er.get(win),
                        "loser_error_rate_slice": er.get(lose),
                        "p_adjusted": _f(getattr(r, "p_adjusted", None)),
                        "diff": diff,
                        "ci_low": _f(r.ci_low),
                        "ci_high": _f(r.ci_high),
                        "p_value": _f(r.p_value),
                        "n_clips": int(r.n_clips),
                        "ci_low_cluster": _f(getattr(r, "ci_low_cluster", None)),
                        "ci_high_cluster": _f(getattr(r, "ci_high_cluster", None)),
                        "p_value_cluster": _f(getattr(r, "p_value_cluster", None)),
                    },
                    sensitive=sens,
                )  # fmt: skip
            )

    overall = agg[agg["slice_kind"] == "all"]
    filt_all = {"tier": tier, "metric": metric, "slice_kind": "all", "slice": "all"}

    for r in overall.itertuples(index=False):
        pid = str(r.provider)
        d = r._asdict()
        # 2. Caption timing.
        tn = int(d.get("timing_n_clips") or 0)
        med, p90, w200 = (
            _f(d.get("timing_median_ms")),
            _f(d.get("timing_p90_ms")),
            _f(d.get("timing_within_200ms")),
        )
        if tn >= min_clips and med is not None:
            text = (
                f"{pid} places words a median {med:.0f} ms from the reference start (p90 {p90:.0f} ms); "
                f"{_pct(w200)} of matched words land within 200 ms, over {tn} clips."
                if p90 is not None and w200 is not None
                else f"{pid} places words a median {med:.0f} ms from the reference start, over {tn} clips."
            )
            out.append(
                _finding(
                    "timing",
                    _slug("timing", pid),
                    tier,
                    text,
                    "aggregates.parquet",
                    filt_all,
                    {
                        "timing_median_ms": med,
                        "timing_p90_ms": p90,
                        "timing_within_200ms": w200,
                        "timing_n_clips": tn,
                        "timing_n_words": int(d.get("timing_n_words") or 0),
                    },
                    provider=pid,
                )  # fmt: skip
            )
        # 3. Caption readiness.
        rn = int(d.get("readiness_n_clips") or 0)
        f1, cas = _f(d.get("punct_f1")), _f(d.get("casing_acc"))
        if rn >= min_clips and f1 is not None:
            extra = f" and casing accuracy {_pct(cas)}" if cas is not None else ""
            out.append(
                _finding(
                    "readiness",
                    _slug("readiness", pid),
                    tier,
                    f"{pid} scores punctuation F1 {f1:.2f}{extra} on {rn} clips with checked punctuation.",
                    "aggregates.parquet",
                    filt_all,
                    {"punct_f1": f1, "casing_acc": cas, "readiness_n_clips": rn},
                    provider=pid,
                )  # fmt: skip
            )
        # 4. Code-switch.
        cn = int(d.get("codeswitch_n_clips") or 0)
        cer_cs = _f(d.get("codeswitch_error_rate"))
        if cn >= min_clips and cer_cs is not None:
            out.append(
                _finding(
                    "codeswitch",
                    _slug("codeswitch", pid),
                    tier,
                    f"On code-switched spans, {pid} has a {metric} of {_pct(cer_cs)} "
                    f"(vs {_pct(_f(r.error_rate))} on whole clips), over {cn} clips.",
                    "aggregates.parquet",
                    filt_all,
                    {
                        "codeswitch_error_rate": cer_cs,
                        "error_rate": _f(r.error_rate),
                        "codeswitch_n_clips": cn,
                        "codeswitch_ref_len": int(d.get("codeswitch_ref_len") or 0),
                    },
                    provider=pid,
                )  # fmt: skip
            )

    # 5. Hallucination over non-speech, overall and over music.
    for kind, slice_ in (("all", "all"), ("audio_condition", "music")):
        part = agg[(agg["slice_kind"] == kind) & (agg["slice"] == slice_)]
        for r in part.itertuples(index=False):
            d = r._asdict()
            nn = int(d.get("nonspeech_n_clips") or 0)
            rate = _f(d.get("halluc_per_nonspeech_min"))
            if nn < min_clips or rate is None:
                continue
            where = "non-speech" if kind == "all" else "non-speech in clips with music"
            text = (
                f"{r.provider} emits {rate:.1f} words per minute of {where} "
                f"({int(d.get('halluc_nonspeech_words') or 0)} words over {_f(d.get('nonspeech_minutes')) or 0:.1f} min, "
                f"{nn} clips)."
            )
            out.append(
                _finding(
                    "hallucination",
                    _slug("halluc", r.provider, slice_ if kind != "all" else ""),
                    tier,
                    text,
                    "aggregates.parquet",
                    {"tier": tier, "metric": metric, "slice_kind": kind, "slice": slice_},
                    {
                        "halluc_per_nonspeech_min": rate,
                        "halluc_nonspeech_words": int(d.get("halluc_nonspeech_words") or 0),
                        "nonspeech_minutes": _f(d.get("nonspeech_minutes")),
                        "nonspeech_n_clips": nn,
                    },
                    provider=str(r.provider),
                )  # fmt: skip
            )

    # 6. Music vs no music, per model (a fact about one model in two conditions, not a model comparison).
    ac = agg[agg["slice_kind"] == "audio_condition"]
    for pid, grp in ac.groupby("provider", sort=True):
        by = {str(x.slice): x for x in grp.itertuples(index=False)}
        m, nm = by.get("music"), by.get("no_music")
        if m is None or nm is None or int(m.n_clips) < min_clips or int(nm.n_clips) < min_clips:
            continue
        em, enm = _f(m.error_rate), _f(nm.error_rate)
        if em is None or enm is None:
            continue
        text = (
            f"{pid} has a {metric} of {_pct(em)} on {int(m.n_clips)} clips with music and {_pct(enm)} on "
            f"{int(nm.n_clips)} clips without (95% CIs {_pct(_f(m.ci_low))} to {_pct(_f(m.ci_high))} and "
            f"{_pct(_f(nm.ci_low))} to {_pct(_f(nm.ci_high))})."
        )
        out.append(
            _finding(
                "music",
                _slug("music-vs-no-music", pid),
                tier,
                text,
                "aggregates.parquet",
                {
                    "tier": tier,
                    "metric": metric,
                    "slice_kind": "audio_condition",
                    "slice": "music",
                },
                {
                    "music_error_rate": em,
                    "no_music_error_rate": enm,
                    "music_n_clips": int(m.n_clips),
                    "no_music_n_clips": int(nm.n_clips),
                },
                provider=str(pid),
            )  # fmt: skip
        )

    # Stable order, unique slugs.
    seen: set[str] = set()
    uniq = []
    order = {"comparison": 0, "music": 1, "hallucination": 2, "timing": 3, "readiness": 4, "codeswitch": 5}
    for f in sorted(out, key=lambda f: (order.get(f["kind"], 9), f["slug"])):
        if f["slug"] in seen:
            continue
        seen.add(f["slug"])
        uniq.append(f)
    return uniq
