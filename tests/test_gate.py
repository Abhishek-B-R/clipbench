"""Publication gate: per provider coverage, eligibility, the verified-set diversity check, the one ranking
order, and that an excluded provider never reaches a ranking, a finding or a "beats" sentence.

The run is synthetic but goes through the real scorer's aggregates and paired bootstrap
(clipbench.scoring.score._aggregates / _paired), so the numbers have the shape a real run produces.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from clipbench.data.check import check_verified, funnel, verified_sample
from clipbench.models import CATEGORIES
from clipbench.report import export, findings, rules
from clipbench.report.report import build_report, load_run
from clipbench.scoring.score import SCORE_COLUMNS, _aggregates, _paired

N = 100
REAL_CATEGORIES = [c for c in CATEGORIES if c != "other"]
FULL = ["p1", "p2", "p3", "p4", "p5", "p6"]
RATES = {"p1": 0.05, "p2": 0.08, "p3": 0.11, "p4": 0.14, "p5": 0.17, "p6": 0.20}


def vid(i: int) -> str:
    return f"v{i:03d}"


def _score_row(pid: str, i: int, rate: float, rng: np.random.Generator) -> dict:
    ref_len = int(20 + (i * 7) % 60)
    errors = int(rng.binomial(ref_len, rate))
    row = dict.fromkeys(SCORE_COLUMNS)
    row.update(
        provider=pid, model_id=f"{pid}-m", video_id=vid(i), tier="verified", language="ja", metric="cer",
        is_primary=True, ref_len=ref_len, errors=errors, error_rate=errors / ref_len, hallucinated_tokens=0,
        hallucination_rate=0.0, long_output=False, latency_ms=1000.0, cost_usd=0.0, audio_seconds=20.0,
        duration_s=20.0, duration_bucket="15-30s", tags="[]", category=REAL_CATEGORIES[i % 6],
        audio_condition="no_music",
    )  # fmt: skip
    return row


def write_run(
    settings,
    cover: dict[str, dict[str, list[int]]] | None = None,
    *,
    full: list[str] = FULL,
    n_channels: int = 40,
    categories: bool = True,
    run_id: str = "g1",
) -> None:
    """A verified run of N clips over `n_channels` channels, with verified files carrying categories.

    `full` providers have ok rows on every clip. `cover` adds providers by status: {"thin": {"ok": [0, 1, 2],
    "skipped": [...], "error": [...]}}; clips with no listed status have no row for that provider."""
    rng = np.random.default_rng(11)
    cover = dict(cover or {})
    plan = {p: {"ok": list(range(N))} for p in full} | cover
    rows, scores = [], []
    for pid, statuses in plan.items():
        rate = RATES.get(pid, 0.01)  # excluded providers look best on the few clips they did, on purpose
        for status, idx in statuses.items():
            for i in idx:
                rows.append({"run_id": run_id, "provider": pid, "model_id": f"{pid}-m", "video_id": vid(i),
                             "language": "ja", "tier": "verified", "repeat": 0, "status": status,
                             "error": None if status == "ok" else f"{status} reason", "channel": f"ch{i % n_channels}",
                             "latency_ms": 1000.0, "audio_seconds": 20.0, "cost_usd": 0.0})  # fmt: skip
                if status == "ok":
                    scores.append(_score_row(pid, i, rate, rng))
    rows_df, scores_df = pd.DataFrame(rows), pd.DataFrame(scores, columns=SCORE_COLUMNS)
    channels = {vid(i): f"ch{i % n_channels}" for i in range(N)}
    res = settings.results_dir / run_id
    res.mkdir(parents=True, exist_ok=True)
    scores_df.to_parquet(res / "scores.parquet")
    _aggregates(scores_df, 7, channels).to_parquet(res / "aggregates.parquet")
    _paired(scores_df, 7, channels).to_parquet(res / "paired.parquet")
    meta = settings.runs_dir / run_id
    meta.mkdir(parents=True, exist_ok=True)
    rows_df.to_parquet(meta / "rows.parquet")
    (meta / "run_meta.json").write_text(json.dumps({"run_id": run_id, "created_at": "2026-10-21T10:00:00Z",
                                                    "language": "ja"}))  # fmt: skip
    settings.verified_dir.mkdir(parents=True, exist_ok=True)
    for i in range(N):
        doc = {"text": "x", "tags": []}
        if categories:
            doc["category"] = REAL_CATEGORIES[i % 6] if i % 10 else "other"
        (settings.verified_dir / f"{vid(i)}.json").write_text(json.dumps(doc))


def status(settings, run_id: str = "g1") -> rules.LanguageStatus:
    res = settings.results_dir / run_id
    return rules.run_status(run_id, "ja", pd.read_parquet(res / "aggregates.parquet"),
                            pd.read_parquet(res / "scores.parquet"), settings)  # fmt: skip


def excluded(st: rules.LanguageStatus) -> dict[str, dict]:
    return {e["provider"]: e for e in st.providers_excluded}


# ---------------------------------------------------------------- coverage and eligibility


def test_full_run_is_published(tmp_settings):
    write_run(tmp_settings)
    st = status(tmp_settings)
    assert st.verified_clips == N and st.providers_eligible == FULL and st.providers_excluded == []
    assert st.min_coverage == 0.9
    assert st.verified_diversity["passes"] and st.verified_diversity["problems"] == []
    assert st.verified_diversity["channels"] == 40 and st.verified_diversity["largest_channel_share"] == 0.03
    assert st.verified_diversity["categories"] == sorted(REAL_CATEGORIES)
    assert st.gate == {"verified_clips_ok": True, "providers_ok": True, "diversity_ok": True}
    assert st.published and st.providers_ok == 6


def test_very_low_coverage_is_excluded_with_counts(tmp_settings):
    """A provider with 3 of 100 clips used to count toward the six; now it is excluded and says why."""
    thin = {"ok": [0, 1, 2], "skipped": list(range(3, 93)), "error": list(range(93, 98))}  # 98, 99: no row
    write_run(tmp_settings, {"thin": thin}, full=FULL[:5])
    st = status(tmp_settings)
    e = excluded(st)["thin"]
    assert (e["coverage"], e["n_scored"], e["n_skipped"], e["n_error"]) == (0.03, 3, 90, 5)
    assert "3.0%" in e["reason"] and "3 of 100" in e["reason"] and "2 with no result" in e["reason"]
    assert "90 skipped, 5 errored" in e["reason"] and "90.0% minimum" in e["reason"]
    assert st.providers_eligible == FULL[:5] and st.providers_ok == 5
    assert not st.published and st.gate["providers_ok"] is False and st.gate["verified_clips_ok"] is True


def test_all_skipped_and_errors_and_missing(tmp_settings):
    write_run(tmp_settings, {
        "skippy": {"skipped": list(range(N))},
        "flaky": {"ok": list(range(80)), "error": list(range(80, N))},
        "ghost": {"ok": [0], "skipped": [1]},  # 98 clips with no row at all
    })  # fmt: skip
    st = status(tmp_settings)
    e = excluded(st)
    assert (e["skippy"]["coverage"], e["skippy"]["n_scored"], e["skippy"]["n_skipped"]) == (0.0, 0, 100)
    assert (e["flaky"]["coverage"], e["flaky"]["n_error"]) == (0.8, 20)
    assert (e["ghost"]["coverage"], e["ghost"]["n_skipped"], e["ghost"]["n_error"]) == (0.01, 1, 0)
    assert "98 with no result" in e["ghost"]["reason"]
    assert st.providers_eligible == FULL and st.published  # six full providers still publish


def test_unequal_evaluation_sets(tmp_settings):
    """A on clips 1..100, B on 1..60: A is eligible, B is not, and B is not compared on its 60 clips."""
    write_run(tmp_settings, {"A": {"ok": list(range(N))}, "B": {"ok": list(range(60))}})
    st = status(tmp_settings)
    assert "A" in st.providers_eligible and "B" not in st.providers_eligible
    assert excluded(st)["B"]["coverage"] == 0.6
    agg = rules.annotate_aggregates(
        pd.read_parquet(tmp_settings.results_dir / "g1" / "aggregates.parquet"), st
    )
    assert set(agg.loc[agg["provider"] == "B", "eligible"]) == {False}
    assert set(agg.loc[agg["provider"] == "B", "coverage"]) == {0.6}
    assert set(agg.loc[agg["provider"] == "A", "coverage"]) == {1.0}


def test_exactly_at_threshold(tmp_settings):
    write_run(tmp_settings, {"at": {"ok": list(range(90))}, "below": {"ok": list(range(89))}})
    st = status(tmp_settings)
    assert "at" in st.providers_eligible and "below" not in st.providers_eligible
    assert excluded(st)["below"]["coverage"] == 0.89


def test_min_coverage_setting(tmp_settings, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "clipbench.toml").write_text("min_provider_coverage = 0.5\n")
    assert rules.min_provider_coverage(replace(tmp_settings, root=root)) == 0.5
    (root / "clipbench.toml").write_text("language = 'ja'\n")
    assert (
        rules.min_provider_coverage(replace(tmp_settings, root=root)) == rules.DEFAULT_MIN_PROVIDER_COVERAGE
    )
    monkeypatch.setenv("CLIPBENCH_MIN_PROVIDER_COVERAGE", "0.55")
    assert rules.min_provider_coverage(replace(tmp_settings, root=root)) == 0.55
    monkeypatch.setenv("CLIPBENCH_MIN_PROVIDER_COVERAGE", "1.5")
    with pytest.raises(ValueError):
        rules.min_provider_coverage(tmp_settings)
    monkeypatch.delenv("CLIPBENCH_MIN_PROVIDER_COVERAGE")
    # The repo setting is what the gate uses by default.
    assert rules.min_provider_coverage(tmp_settings) == 0.9

    monkeypatch.setenv("CLIPBENCH_MIN_PROVIDER_COVERAGE", "0.5")
    write_run(tmp_settings, {"B": {"ok": list(range(60))}})
    assert "B" in status(tmp_settings).providers_eligible


# ---------------------------------------------------------------- diversity on the verified set


def test_diversity_counts_only_verified_file_categories(tmp_settings):
    """scores.parquet has six categories (from the manifest), but the verified files have none: fail."""
    write_run(tmp_settings, categories=False)
    st = status(tmp_settings)
    assert st.categories == sorted(REAL_CATEGORIES)  # legacy descriptive field, guesses included
    assert st.verified_diversity["categories"] == [] and not st.verified_diversity["passes"]
    assert any("categories" in p for p in st.verified_diversity["problems"])
    assert st.gate == {"verified_clips_ok": True, "providers_ok": True, "diversity_ok": False}
    assert not st.published


def test_diversity_channel_rules(tmp_settings):
    write_run(tmp_settings, n_channels=10)  # 10 channels, 10% each
    div = status(tmp_settings).verified_diversity
    assert div["channels"] == 10 and div["largest_channel_share"] == 0.1 and not div["passes"]
    assert any("10 channels" in p for p in div["problems"]) and any("10.0%" in p for p in div["problems"])


def test_check_verified_and_cli(tmp_settings):
    write_run(tmp_settings)
    clips = pd.DataFrame(
        {"video_id": [vid(i) for i in range(N)], "channel": [f"ch{i % 40}" for i in range(N)]}
    )
    assert check_verified(clips, tmp_settings.verified_dir).ok
    # A guessed category in the input frame is ignored: only the verified file counts.
    guess = clips.assign(category="gaming", category_guess=True)
    gaming = sum(1 for i in range(N) if i % 10 and REAL_CATEGORIES[i % 6] == "gaming")
    assert check_verified(guess, tmp_settings.verified_dir).categories["gaming"] == gaming

    # clipbench data check --verified reads the sample and keeps only usable verified clips.
    sample = clips.assign(language="ja", sample_rank=range(N), channel_id=None)
    sample = pd.concat([sample, pd.DataFrame([{"video_id": "skip1", "channel": "chX", "language": "ja",
                                               "sample_rank": N}])])  # fmt: skip
    (tmp_settings.verified_dir / "skip1.json").write_text(json.dumps({"skipped": True}))
    tmp_settings.data_dir.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(tmp_settings.sample_path)
    assert len(verified_sample(sample, tmp_settings.verified_dir)) == N
    from clipbench.cli import app

    res = CliRunner().invoke(app, ["data", "check", "--lang", "ja", "--verified"])
    assert res.exit_code == 0, res.output
    assert "verified clips of the sample: 100 clips, 40 channels" in res.output


def test_funnel(tmp_settings):
    vids = ["a", "b", "c", "d", "e"]
    sample = pd.DataFrame({"video_id": vids})
    audio = tmp_settings.audio_dir
    audio.mkdir(parents=True)
    for v in ("a", "b", "c", "d"):
        (audio / f"{v}.wav").write_bytes(b"RIFF")
    hashes = {v: {"wav": "h"} for v in ("a", "b", "c")}  # d has a WAV but no hash
    ver = tmp_settings.verified_dir
    ver.mkdir(parents=True)
    (ver / "a.json").write_text(json.dumps({"text": "ok"}))
    (ver / "b.json").write_text(json.dumps({"skipped": True}))
    (ver / "c.json").write_text(json.dumps({"text": "x", "tags": ["unusable"]}))
    (ver / "d.json").write_text(json.dumps({"text": "  "}))  # reviewed, empty text: not verified
    assert funnel(sample, audio, ver, hashes) == {"sampled": 5, "audio_available": 3, "reviewed": 4,
                                                  "skipped": 1, "unusable": 1, "verified": 1}  # fmt: skip


# ---------------------------------------------------------------- excluded providers stay out


def test_ranking_order_is_defined_once():
    rows = pd.DataFrame({"provider": ["b", "a", "c", "d"], "error_rate": [0.1, 0.1, 0.05, float("nan")],
                         "tier": "verified", "metric": "cer", "slice_kind": "all", "ci_low": 0.0,
                         "ci_high": 0.2, "n_clips": 100})  # fmt: skip
    ranked = rules.ranking(rows, {"a", "b", "c", "d"}, "verified", "cer")
    assert [(r["provider"], r["rank"]) for r in ranked] == [("c", 1), ("a", 2), ("b", 3)]  # tie: by id
    assert [r["provider"] for r in rules.ranking(rows, {"a", "b"}, "verified", "cer")] == ["a", "b"]


def test_excluded_provider_never_ranked_or_in_findings(tmp_settings, tmp_path, monkeypatch):
    """`thin` scores best on its 3 clips and `half` on its 50; neither may appear in the ranking, the
    findings, any "beats" sentence, the report's tables or the site's leaderboard."""
    monkeypatch.setenv("CLIPBENCH_WEB_DIR", str(tmp_path / "web"))
    write_run(tmp_settings, {"thin": {"ok": [0, 1, 2], "skipped": list(range(3, N))},
                             "half": {"ok": list(range(50))}})  # fmt: skip
    data = load_run("g1", tmp_settings)
    assert data.published and data.eligible == set(FULL)
    raw_paired = pd.read_parquet(tmp_settings.results_dir / "g1" / "paired.parquet")
    assert {"thin", "half"} & (set(raw_paired["provider_a"]) | set(raw_paired["provider_b"]))  # tests exist

    for line in findings.draft_findings(data.aggregates, data.paired, data.stability, "verified", "cer",
                                        eligible=data.eligible):  # fmt: skip
        assert "thin" not in line and "half" not in line
    # Even when handed every provider's rows, eligible= keeps them out.
    every = findings.structured_findings(data.all_aggregates, data.all_paired, "cer", eligible=data.eligible)
    assert every and any(f["kind"] == "comparison" for f in every)
    assert not any("thin" in f["text"] or "half" in f["text"] for f in every)

    run_path, _ = export.export_run("g1", tmp_settings)
    run = json.loads(run_path.read_text())
    lang = run["languages"][0]
    assert lang["providers_eligible"] == FULL and {e["provider"] for e in lang["providers_excluded"]} == {
        "half",
        "thin",
    }
    assert [r["provider"] for r in run["ranking"]["verified"]["cer"]] == FULL  # p1 best ... p6 worst
    assert {(a["provider"], a["eligible"]) for a in run["aggregates"] if a["provider"] in ("thin", "half")} == {
        ("thin", False), ("half", False)}  # fmt: skip
    assert not {p["provider_a"] for p in run["paired"]} & {"thin", "half"}
    assert not {p["provider_b"] for p in run["paired"]} & {"thin", "half"}
    assert all("verdict_raw" in p and "p_adjusted" in p for p in run["paired"])
    assert not any("thin" in f["text"] or "half" in f["text"] for f in run["findings"])
    doc = json.loads((export.data_dir(tmp_settings) / "findings" / "ja.json").read_text())
    assert doc["findings"] and not any("thin" in f["text"] or "half" in f["text"] for f in doc["findings"])

    md_path, html_path = build_report("g1", tmp_settings)
    md, page = md_path.read_text(), html_path.read_text()
    beats = [ln for ln in md.splitlines() if "beats" in ln]
    assert beats and not any("thin" in ln or "half" in ln for ln in beats)
    headline = md.split("## Verified references", 1)[1].split("_Tier:", 1)[0]
    assert "| thin |" not in headline and "| half |" not in headline and "| p1 |" in headline
    assert "Excluded from every ranking" in md and "thin: coverage 3.0%" in md
    assert "scored: thin | 3 of 100 verified (3%, excluded)" in md
    assert 'data-provider="thin"' not in page and 'data-provider="half"' not in page
    assert 'data-provider="p1"' in page


def test_cluster_sensitive_beats_is_flagged():
    agg = pd.DataFrame([{"provider": p, "model_id": p, "tier": "verified", "metric": "cer", "slice_kind": "all",
                         "slice": "all", "n_clips": 100, "error_rate": e, "ci_low": e - 0.01, "ci_high": e + 0.01,
                         "too_small": False} for p, e in (("a", 0.05), ("b", 0.10))])  # fmt: skip
    base = {"tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": "a",
            "provider_b": "b", "n_clips": 100, "diff": -0.05, "ci_low": -0.08, "ci_high": -0.02, "p_value": 0.001,
            "verdict": "a_better"}  # fmt: skip
    robust = pd.DataFrame(
        [base | {"ci_low_cluster": -0.09, "ci_high_cluster": -0.01, "p_value_cluster": 0.01}]
    )
    shaky = pd.DataFrame([base | {"ci_low_cluster": -0.10, "ci_high_cluster": 0.01, "p_value_cluster": 0.08}])

    f_robust = [f for f in findings.structured_findings(agg, robust, "cer") if f["kind"] == "comparison"][0]
    f_shaky = [f for f in findings.structured_findings(agg, shaky, "cer") if f["kind"] == "comparison"][0]
    assert f_robust["cluster_sensitive"] is False and "channel" not in f_robust["text"]
    assert f_shaky["cluster_sensitive"] is True
    assert "Sensitive to channel clustering" in f_shaky["text"] and "-1.0 and 10.0 points" in f_shaky["text"]
    assert f_shaky["numbers"]["ci_high_cluster"] == 0.01

    line = findings.compare_sentence(shaky, "verified", "cer", "all", "all", "a", "b", "overall")
    assert "a beats b" in line and "Sensitive to channel clustering" in line
    line = findings.compare_sentence(robust, "verified", "cer", "all", "all", "a", "b", "overall")
    assert "a beats b" in line and "clustering" not in line
