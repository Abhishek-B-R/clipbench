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


PASSING_DIVERSITY = {"channels": 30, "largest_channel_share": 0.05, "categories": ["a", "b", "c", "d", "e", "f"],
                     "passes": True, "problems": []}  # fmt: skip


@pytest.fixture
def published_rules(monkeypatch):
    """The synthetic run has 60 verified clips and 3 providers and no verified files: lower the bar and stub
    the verified-set diversity check (tested on its own in tests/test_gate.py) so it counts as published."""
    monkeypatch.setattr(rules, "MIN_VERIFIED_CLIPS", 50)
    monkeypatch.setattr(rules, "MIN_PROVIDERS_OK", 3)
    monkeypatch.setattr(rules, "verified_diversity", lambda ids, channels, settings: dict(PASSING_DIVERSITY))


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
    # neither youtube-auto nor wayin is in this run: the report must not name systems it did not test
    assert "Wayin" not in md and "systems under test" not in md

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
    assert "Not published yet: 60 of 100 verified clips, 3 of 6 eligible providers" in md
    assert "verified-set diversity: no verified clips" in md  # no rows.parquet, no verified files
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
    assert rules.is_published(100, 6, True) and not rules.is_published(99, 6, True)
    assert not rules.is_published(100, 5, True) and not rules.is_published(100, 6, False)


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
    assert "youtube-auto scored 0 of 3 clips, skipped on 3 (no auto track)" in md
    assert "YouTube auto captions are a system under test, never a reference." in md
    assert "alpha scored" not in md


def test_fdr_correction_demotes_weak_wins_across_many_tests():
    import pandas as pd

    from clipbench.report.findings import adjust_paired

    rows = [
        {"tier": "verified", "metric": "cer", "slice_kind": "tag", "slice": f"s{i}", "provider_a": "a",
         "provider_b": "b", "n_clips": 40, "diff": -0.02, "ci_low": -0.04, "ci_high": -0.001,
         "p_value": 0.04, "verdict": "a_better"}
        for i in range(20)
    ] + [
        {"tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": "a",
         "provider_b": "b", "n_clips": 150, "diff": -0.08, "ci_low": -0.11, "ci_high": -0.05,
         "p_value": 0.0005, "verdict": "a_better"},
    ] + [
        {"tier": "verified", "metric": "cer", "slice_kind": "category", "slice": f"c{i}", "provider_a": "a",
         "provider_b": "b", "n_clips": 40, "diff": 0.0, "ci_low": -0.03, "ci_high": 0.03,
         "p_value": 0.5, "verdict": "no_difference"}
        for i in range(30)
    ]  # fmt: skip
    out = adjust_paired(pd.DataFrame(rows))
    strong = out[out["slice_kind"] == "all"].iloc[0]
    assert strong["verdict"] == "a_better" and strong["p_adjusted"] <= 0.05
    weak = out[out["slice_kind"] == "tag"]
    assert (weak["verdict"] == "no_difference").all()  # 20 of 51 tests at p=0.04: none survive BH at q=0.05
    assert (weak["verdict_raw"] == "a_better").all()
    assert adjust_paired(out) is out  # idempotent


def test_comparison_finding_quotes_only_common_clip_numbers():
    import pandas as pd

    from clipbench.report.findings import structured_findings

    agg = pd.DataFrame([
        {"provider": "a", "tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all",
         "n_clips": 150, "error_rate": 0.10},
        {"provider": "b", "tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all",
         "n_clips": 120, "error_rate": 0.30},
    ])  # fmt: skip
    paired = pd.DataFrame([
        {"tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": "a",
         "provider_b": "b", "n_clips": 120, "diff": -0.08, "ci_low": -0.11, "ci_high": -0.05,
         "p_value": 0.0005, "verdict": "a_better"},
    ])  # fmt: skip
    f = [x for x in structured_findings(agg, paired, "cer") if "beats" in x["text"]][0]
    assert "8.0 points fewer errors" in f["text"] and "120 clips both scored" in f["text"]
    assert "10.0%" not in f["text"] and "30.0%" not in f["text"]  # slice-wide rates cover different clips


def test_quota_and_budget_stops_read_as_not_run():
    """An account out of quota is not a model failure: the limitation line must say 'not run (account
    quota)', distinguish other skips and errors, and never print an error message body."""
    from clipbench.report.report import coverage_notes

    rows = pd.DataFrame(
        [{"provider": "cf", "video_id": f"v{i}", "repeat": 0, "status": "ok", "error": None} for i in range(2)]
        + [{"provider": "cf", "video_id": f"q{i}", "repeat": 0, "status": "skipped", "error": "quota"} for i in range(3)]
        + [{"provider": "cf", "video_id": "b0", "repeat": 0, "status": "skipped", "error": "budget"}]
        + [{"provider": "cf", "video_id": "e0", "repeat": 0, "status": "error",
            "error": "HTTPStatusError: 500 body says /Users/me/secret/path.wav"}]
        + [{"provider": "cf", "video_id": "q0", "repeat": 1, "status": "skipped", "error": "quota"}]
    )  # fmt: skip
    note = coverage_notes(rows)["cf"]
    assert note == ("cf scored 2 of 7 clips, not run on 3 (account quota), not run on 1 (budget cap reached), "
                    "failed on 1 (HTTPStatusError)")  # fmt: skip
    assert "/Users" not in note and "body says" not in note


def test_excluded_reason_names_quota_without_verified_clips():
    from clipbench.report import rules

    rows = pd.DataFrame(
        [{"provider": "g", "video_id": "v0", "repeat": 0, "status": "ok", "error": None, "tier": "creator"}]
        + [{"provider": "g", "video_id": f"v{i}", "repeat": 0, "status": "skipped", "error": "quota",
            "tier": "creator"} for i in range(1, 5)]
    )  # fmt: skip
    cov = rules.provider_coverage(rows, set(), pd.DataFrame(), 0.9)
    assert cov["g"].reason == "no verified clips in this run; on the clips it was given: not run on 4 (account quota)"


def test_creator_only_report_reads_correctly(tmp_settings):
    """A creator-tier-only run (nothing verified): the funnel shows each provider's results on the run's
    clips instead of '0 of 0 (n/a)', the empty caption-metrics table becomes one sentence, and the
    lede names windows cut from longer videos."""
    _write_run(tmp_settings)
    res = tmp_settings.results_dir / "r1"
    agg = pd.read_parquet(res / "aggregates.parquet")
    agg = agg[agg["tier"] == "creator"]
    for col in ("timing_n_clips", "nonspeech_n_clips", "readiness_n_clips", "codeswitch_n_clips"):
        agg[col] = 0
    agg.to_parquet(res / "aggregates.parquet")
    rows = pd.DataFrame(
        [{"provider": "alpha", "video_id": f"v{i}@1000-30000", "repeat": 0, "status": "ok", "error": None,
          "tier": "creator"} for i in range(3)]
        + [{"provider": "beta", "video_id": f"v{i}@1000-30000", "repeat": 0,
            "status": "ok" if i == 0 else "skipped", "error": None if i == 0 else "quota", "tier": "creator"}
           for i in range(3)]
    )  # fmt: skip
    rows.to_parquet(tmp_settings.runs_dir / "r1" / "rows.parquet")
    md = build_report("r1", tmp_settings)[0].read_text()
    assert "0 of 0" not in md and "(n/a, excluded)" not in md
    assert "| results: alpha | 3 of 3 clips in this run (not eligible: nothing verified) |" in md
    assert "| results: beta | 1 of 3 clips in this run (not eligible: nothing verified) |" in md
    assert "beta scored 1 of 3 clips, not run on 2 (account quota)" in md
    assert "Not measured on this tier" in md and "| timing median / p90 |" not in md


def test_site_creator_only_notes(tmp_settings):
    """The static site mirrors the report: no caption table of n/a but one sentence, round thresholds
    without a decimal, and a labelled latency line in the run block."""
    _write_run(tmp_settings)
    res = tmp_settings.results_dir / "r1"
    agg = pd.read_parquet(res / "aggregates.parquet")
    agg = agg[agg["tier"] == "creator"]
    for col in ("timing_n_clips", "nonspeech_n_clips", "readiness_n_clips", "codeswitch_n_clips"):
        agg[col] = 0
    agg.to_parquet(res / "aggregates.parquet")
    (res / "errors.json").write_text(json.dumps({"providers": {}, "examples": [], "all_fail": ["a", "b"],
                                                  "all_fail_threshold": 0.5}))  # fmt: skip
    md, html_path = build_report("r1", tmp_settings)
    page = html_path.read_text()
    assert "Not measured in this run" in page
    assert "above 50% error" in page and "50.0%" not in page
    assert "<dt>latency</dt>" in page and "contended" in page
    assert "above 50% error" in md.read_text()


def test_lede_names_windows_and_category_guesses_are_flagged(tmp_settings):
    """The first real run was 127 windows cut from long videos and 23 Shorts, with every category guessed
    from the channel: the report must not call it 'YouTube Shorts' or present guesses as labels."""
    from clipbench.report.report import _clip_sources, guessed_categories

    s = pd.DataFrame({"video_id": ["a", "b@1000-20000"]})
    assert _clip_sources(s) == "YouTube Shorts and short windows cut from longer YouTube videos"
    assert _clip_sources(pd.DataFrame({"video_id": ["a"]})) == "YouTube Shorts"
    tmp_settings.data_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"video_id": ["a", "b@1000-20000"], "clip_id": ["a", "b@1000-20000"], "language": ["ja", "ja"],
                  "sample_rank": [0, 1], "category": ["gaming", "vlog_music"],
                  "category_guess": [True, False]}).to_parquet(tmp_settings.sample_path)  # fmt: skip
    scores = pd.DataFrame({"video_id": ["a", "b@1000-20000"], "tier": ["creator", "creator"],
                           "category": ["gaming", "vlog_music"]})  # fmt: skip
    assert guessed_categories(tmp_settings, "ja", scores) == (1, 2)
