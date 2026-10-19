from __future__ import annotations

import json
import re

import pandas as pd

from clipbench.report import findings
from clipbench.report.report import build_report

PROVIDERS = {
    "alpha": ("alpha-1", 0.10, 0.36, 900.0),
    "beta": ("beta-2", 0.12, 0.0, 1500.0),
    "gamma": ("gamma-3", 0.30, 0.60, 400.0),
}


def _agg_rows():
    rows = []
    for tier, shift in (("verified", 0.0), ("creator", 0.05)):
        for (pid, (model, er, cost, lat)), (metric, moff) in (
            (item, m) for item in PROVIDERS.items() for m in (("cer", 0.0), ("cer_reading", -0.02))
        ):
            base = {
                "provider": pid, "model_id": model, "tier": tier, "metric": metric,
                "hallucination_rate": 0.01 if pid != "gamma" else 0.08, "long_output_rate": 0.02,
                "cost_usd_total": cost * 0.5, "audio_hours": 0.5, "cost_per_hour": cost,
                "latency_p50": lat, "latency_p95": lat * 2, "rtf": lat / 30000,
            }  # fmt: skip
            slices = [("all", "all", 60, False), ("tag", "background_music", 20, False),
                      ("tag", "noisy", 4, True), ("tag", "fast_speech", 18, False), ("duration", "<15s", 25, False),
                      ("duration", "15-30s", 30, False), ("duration", "30-60s", 5, True)]  # fmt: skip
            for kind, sl, n, small in slices:
                e = er + shift + moff + (0.05 if sl == "background_music" and pid == "alpha" else 0.0)
                e += 0.06 if sl == "fast_speech" and pid == "beta" else 0.0
                rows.append(
                    {
                        **base,
                        "slice_kind": kind,
                        "slice": sl,
                        "n_clips": n,
                        "error_rate": e,
                        "ci_low": e - 0.03,
                        "ci_high": e + 0.03,
                        "too_small": small,
                    }  # fmt: skip
                )
    return pd.DataFrame(rows)


def _paired():
    rows = []
    for tier in ("verified", "creator"):
        rows += [
            # alpha vs beta: not detectably different; alpha vs gamma: alpha better
            {"tier": tier, "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": "alpha",
             "provider_b": "beta", "n_clips": 60, "diff": -0.02, "ci_low": -0.05, "ci_high": 0.01,
             "p_value": 0.2, "verdict": "no_difference"},
            {"tier": tier, "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": "gamma",
             "provider_b": "alpha", "n_clips": 60, "diff": 0.2, "ci_low": 0.15, "ci_high": 0.25,
             "p_value": 0.001, "verdict": "b_better"},
            {"tier": tier, "metric": "cer", "slice_kind": "tag", "slice": "background_music",
             "provider_a": "beta", "provider_b": "alpha", "n_clips": 20, "diff": -0.03, "ci_low": -0.08,
             "ci_high": 0.02, "p_value": 0.3, "verdict": "no_difference"},
        ]  # fmt: skip
    return pd.DataFrame(rows)


def _write_run(settings, run_id="r1"):
    res = settings.results_dir / run_id
    res.mkdir(parents=True)
    _agg_rows().to_parquet(res / "aggregates.parquet")
    _paired().to_parquet(res / "paired.parquet")
    pd.DataFrame(
        [
            {
                "provider": p,
                "video_id": f"v{i}",
                "n_repeats": 3,
                "text_disagreement": d,
                "latency_mean": 1.0,
                "latency_std": 0.1,
            }
            for p, d in (("alpha", 0.01), ("beta", 0.04))
            for i in range(3)
        ]  # fmt: skip
    ).to_parquet(res / "stability.parquet")
    (res / "errors.json").write_text(json.dumps({
        "providers": {"alpha": {"top_substitutions": [["きょう", "今日", 3]], "worst_clips": []},
                      "beta": {"top_substitutions": [], "worst_clips": []}},
        "all_fail": ["vbad"], "all_fail_threshold": 0.5,
        "examples": [{"provider": "alpha", "video_id": "v1", "ref_snippet": "きょうは", "hyp_snippet": "今日は"}],
    }, ensure_ascii=False))  # fmt: skip
    meta_dir = settings.runs_dir / run_id
    meta_dir.mkdir(parents=True)
    (meta_dir / "run_meta.json").write_text(json.dumps({
        "run_id": run_id, "created_at": "2026-10-19T10:00:00Z", "git_commit": "abc1234", "git_dirty": False,
        "sample_seed": 7, "stability_seed": 7, "language": "ja",
        "providers": [{"id": p, "model_id": m, "pricing_checked_on": "2026-10-19"} for p, (m, *_ ) in PROVIDERS.items()],
    }))  # fmt: skip


def test_report_renders(tmp_settings):
    _write_run(tmp_settings)
    md_path, html_path = build_report("r1", tmp_settings)
    md = md_path.read_text()
    page = html_path.read_text()

    assert "## Verified references" in md and "## Creator captions (unverified reference)" in md
    assert md.index("Verified references") < md.index("Creator captions")
    assert "unverified reference" in md.lower()
    for pid, (model, *_rest) in PROVIDERS.items():
        assert pid in md and model in md
    assert "too small to conclude" in md
    assert "abc1234" in md and "2026-10-19" in md
    assert "YouTube auto captions and Wayin are systems under test" in md

    # Findings: every line drafted, sourced, and "beats" only where the paired test says so.
    lines = [line[2:] for line in md.splitlines() if line.startswith("- DRAFT")]
    assert lines
    for line in lines:
        assert line.startswith("DRAFT: verify.")
        assert "(source: " in line
    beats = [line for line in lines if "beats" in line]
    assert beats and all("alpha beats gamma" in line for line in beats)
    assert any("No detectable difference between alpha and beta" in line for line in lines)
    assert any("background_music" in line and "No detectable difference" in line for line in lines)
    assert not any("beats" in line and "beta" in line for line in lines)

    # Charts: PNGs on both sides, specs inline, pinned CDN.
    for name in (
        "accuracy_vs_cost_verified",
        "by_tag_verified",
        "hallucination_verified",
        "by_duration_verified",
        "accuracy_vs_cost_creator",
    ):
        assert (tmp_settings.site_dir / "charts" / f"{name}.png").read_bytes()[:4] == b"\x89PNG"
        assert (tmp_settings.results_dir / "r1" / "charts" / f"{name}.png").exists()
        assert f"charts/{name}.png" in md
    assert "cdn.jsdelivr.net/npm/vega-embed@6.26.0" in page
    assert 'id="chart-specs"' in page and "prefers-color-scheme: dark" in page
    # Site layout: hero, leaderboard per tier, heatmap, unverified label, draft findings.
    assert 'class="hero' in page and 'class="lb sortable"' in page and 'class="hm"' in page
    assert 'data-tier="verified"' in page and 'data-tier="creator"' in page
    assert "unverified reference" in page and "too small to conclude" in page
    assert page.count("DRAFT: verify.") >= len(lines) // 2
    assert "alpha beats gamma" in page

    for text in (md, page):
        assert not re.search("[" + chr(0x2013) + chr(0x2014) + "]", text), "no en or em dashes"


def test_beats_requires_paired_verdict():
    agg = _agg_rows()
    lines = findings.draft_findings(agg, None, None, "verified", "cer")
    assert lines and not any("beats" in line for line in lines)
    assert all(line.startswith("DRAFT: verify.") for line in lines)
