from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from clipbench.report import findings, rules
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
                      ("duration", "15-30s", 30, False), ("duration", "30-60s", 5, True),
                      ("category", "talking_head", 40, False), ("category", "gaming", 6, True),
                      ("audio_condition", "music", 20, False), ("audio_condition", "no_music", 40, False)]  # fmt: skip
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
                        **_v2(pid, n),
                    }  # fmt: skip
                )
    return pd.DataFrame(rows)


def _v2(pid, n):
    """v2 caption metrics. gamma has no word timings (null, must show as n/a, never 0)."""
    timed = pid != "gamma"
    return {
        "timing_n_clips": n if timed else 0, "timing_n_words": 30 * n if timed else 0,
        "timing_median_ms": 80.0 if timed else None, "timing_p90_ms": 240.0 if timed else None,
        "timing_within_200ms": 0.85 if timed else None,
        "nonspeech_n_clips": n, "nonspeech_minutes": n * 0.1, "halluc_nonspeech_words": n * (2 if pid == "gamma" else 0),
        "halluc_per_nonspeech_min": 20.0 if pid == "gamma" else 0.0,
        "readiness_n_clips": n, "punct_f1": 0.71, "casing_acc": None,
        "codeswitch_n_clips": 0, "codeswitch_ref_len": 0, "codeswitch_errors": 0, "codeswitch_error_rate": None,
    }  # fmt: skip


@pytest.fixture
def published_rules(monkeypatch):
    """The synthetic run has 60 verified clips and 3 providers: lower the bar so it counts as published."""
    monkeypatch.setattr(rules, "MIN_VERIFIED_CLIPS", 50)
    monkeypatch.setattr(rules, "MIN_PROVIDERS_OK", 3)


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
            {"tier": tier, "metric": "cer", "slice_kind": "audio_condition", "slice": "music",
             "provider_a": "alpha", "provider_b": "gamma", "n_clips": 20, "diff": -0.2, "ci_low": -0.25,
             "ci_high": -0.15, "p_value": 0.002, "verdict": "a_better"},
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


def test_report_renders(tmp_settings, published_rules):
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


def test_unpublished_report_has_no_ranking_or_findings(tmp_settings):
    _write_run(tmp_settings)  # 60 verified clips, 3 providers: below the real bar
    md_path, html_path = build_report("r1", tmp_settings)
    md, page = md_path.read_text(), html_path.read_text()
    assert "Not published yet: 60 of 100 verified clips and 3 of 6 providers" in md
    assert not [line for line in md.splitlines() if line.startswith("- DRAFT")]
    assert "beats" not in md.replace("one provider beats another", "")
    # Headline rows in provider order, not by error rate (gamma is worst but still listed last by name).
    table = md.split("## Verified references", 1)[1].split("_Tier:", 1)[0]
    order = [
        line.split("|")[1].strip()
        for line in table.splitlines()
        if line.startswith("| ") and "provider" not in line
    ]
    assert order == sorted(order)
    assert "Not a ranking" in md
    assert "Lowest CER" not in page and "first results once 100 clips are verified" in page
    assert "Results so far (not a ranking)" in page and "<h2>Leaderboard</h2>" not in page
    assert "unverified reference" in page.lower()
    assert 'class="verdict best"' not in page and 'best"' not in page


def test_caption_metrics_show_na_not_zero(tmp_settings, published_rules):
    _write_run(tmp_settings)
    md = build_report("r1", tmp_settings)[0].read_text()
    section = md.split("### Caption metrics", 1)[1].split("_Tier", 1)[0]
    gamma = next(line for line in section.splitlines() if line.startswith("| gamma"))
    assert gamma.split("|")[2].strip() == "n/a" and gamma.split("|")[3].strip() == "n/a"
    alpha = next(line for line in section.splitlines() if line.startswith("| alpha"))
    assert "80 ms / 240 ms" in alpha
    assert "### By content category" in md and "### Music vs no music" in md
    assert "talking_head" in md and "too small to conclude" in md.split("### By content category", 1)[1]


def test_structured_findings_rules():
    agg, paired = _agg_rows(), _paired()
    items = findings.structured_findings(agg, paired, "cer", tier="verified", min_clips=15)
    assert items
    for f in items:
        assert f["status"] == "draft" and "DRAFT" not in f["text"]
        assert set(f) >= {"id", "slug", "kind", "text", "source", "numbers"}
        assert f["source"]["table"] in {"paired", "aggregates"}
        assert re.fullmatch(r"[a-z0-9-]+", f["slug"])
        allowed = {"tier", "metric", "slice_kind", "slice", "provider_a", "provider_b", "provider", "sort"}
        assert set(f["source"]["filters"]) <= allowed
        assert f["source"]["filters"].get("sort") in {
            None,
            "timing",
            "offset",
            "halluc",
            "punct",
            "casing",
            "codeswitch",
        }
        if "beats" in f["text"]:
            assert f["kind"] == "comparison"
    comps = [f for f in items if f["kind"] == "comparison"]
    # Only the two a_better/b_better rows survive; no_difference rows are not findings.
    assert {f["slug"] for f in comps} == {
        "cer-alpha-beats-gamma",
        "cer-alpha-beats-gamma-audio-condition-music",
    }
    music = next(f for f in comps if "music" in f["slug"])
    assert music["source"]["filters"]["slice"] == "music" and music["numbers"]["p_value"] == 0.002
    assert music["source"]["filters"]["provider_a"] == "alpha" and music["source"]["table"] == "paired"
    timing_alpha = next(f for f in items if f["slug"] == "timing-alpha")
    assert timing_alpha["source"]["filters"] == {"tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all",
                                                 "provider": "alpha", "sort": "timing"}  # fmt: skip
    timing = {f["slug"] for f in items if f["kind"] == "timing"}
    assert timing == {"timing-alpha", "timing-beta"}  # gamma has no word timings
    assert any(f["kind"] == "hallucination" and "gamma" in f["slug"] for f in items)
    assert any(f["kind"] == "music" for f in items)
    # Stable: same input, same ids.
    again = findings.structured_findings(agg, paired, "cer", tier="verified", min_clips=15)
    assert [f["id"] for f in again] == [f["id"] for f in items]
    # Too small: nothing passes a bar above every slice.
    assert findings.structured_findings(agg, paired, "cer", min_clips=1000) == []


def test_rules_language_status(tmp_settings):
    _write_run(tmp_settings)
    st = rules.run_status("r1", "ja", pd.read_parquet(tmp_settings.results_dir / "r1" / "aggregates.parquet"), None,
                          tmp_settings)  # fmt: skip
    assert (st.verified_clips, st.providers_ok, st.published) == (60, 3, False)
    langs = rules.all_languages({"ja": st})
    assert [entry["lang"] for entry in langs] == ["en", "ja", "ko", "es", "hi"]
    assert langs[0] == rules.LanguageStatus(lang="en").as_dict()
    assert rules.is_published(100, 6) and not rules.is_published(99, 6) and not rules.is_published(100, 5)


def test_limitations_name_providers_skipped_on_clips(tmp_settings):
    """A provider skipped on every clip (youtube-auto with no auto tracks) silently vanishes from every
    table; the limitations must say so, with the reason."""
    _write_run(tmp_settings)
    rows = [
        {"provider": "alpha", "video_id": f"v{i}", "repeat": 0, "status": "ok", "error": None, "tier": "verified"}
        for i in range(3)
    ] + [
        {"provider": "youtube-auto", "video_id": f"v{i}", "repeat": 0, "status": "skipped", "error": "no auto track",
         "tier": "verified"}
        for i in range(3)
    ]  # fmt: skip
    pd.DataFrame(rows).to_parquet(tmp_settings.runs_dir / "r1" / "rows.parquet")
    md_path, _ = build_report("r1", tmp_settings)
    md = md_path.read_text()
    assert "youtube-auto scored 0 of 3 clips (3 skipped: no auto track)" in md
    assert "alpha scored" not in md
