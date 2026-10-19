from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench.scoring.lid import export_section, find_hinted_runs, score_lid
from tests.fixtures.provider_runs import write_run

CLIPS = [f"c{i}" for i in range(4)]


def write_scores(settings, run_id, provider, errors, ref_len=10):
    out = settings.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "scores.parquet"
    df = pd.DataFrame(
        [
            {"provider": provider, "video_id": v, "metric": "cer", "is_primary": True, "errors": e,
             "ref_len": ref_len, "error_rate": e / ref_len}
            for v, e in zip(CLIPS, errors, strict=True)
        ]
    )
    if path.exists():
        df = pd.concat([pd.read_parquet(path), df])
    df.to_parquet(path, index=False)


@pytest.fixture
def runs(tmp_settings):
    refs = [{"video_id": v, "text": "x", "conditions": ["music"] if v == "c0" else []} for v in CLIPS]
    # hinted run, older
    write_run(tmp_settings, "20261001-0000-aaaa", [{"provider": "p", "video_id": v} for v in CLIPS], refs,
              language="ja")
    write_scores(tmp_settings, "20261001-0000-aaaa", "p", [1, 1, 1, 1])
    # detect run: p gets 3 of 4 right; q never reports a language (no detection offered)
    detected = ["ja", "ja-JP", "jpn", "ko"]
    hyps = [{"provider": "p", "video_id": v, "detected_language": d} for v, d in zip(CLIPS, detected, strict=True)]
    hyps += [{"provider": "q", "video_id": v} for v in CLIPS]
    write_run(tmp_settings, "20261002-0000-bbbb", hyps, refs, language="ja", options={"detect_language": True})
    write_scores(tmp_settings, "20261002-0000-bbbb", "p", [1, 1, 1, 9])
    return tmp_settings


def test_lid_accuracy_and_hinted_comparison(runs):
    path = score_lid("20261002-0000-bbbb", runs)
    clips = pd.read_parquet(path)
    assert set(clips["provider"]) == {"p"}
    assert clips["correct"].tolist() == [True, True, True, False]
    assert clips["hinted_run_id"].eq("20261001-0000-aaaa").all()
    agg = pd.read_parquet(runs.results_dir / "20261002-0000-bbbb" / "lid_aggregates.parquet")
    row = agg[agg.slice == "all"].iloc[0]
    assert row.lid_accuracy == pytest.approx(0.75)
    assert row.error_rate_detect == pytest.approx(12 / 40)
    assert row.error_rate_hinted == pytest.approx(4 / 40)
    assert row.error_rate_diff == pytest.approx(8 / 40)
    assert row.n_paired == 4
    assert row.wrong_language_error_rate == pytest.approx(0.9)
    assert "music" in set(agg["slice"])
    section = export_section("20261002-0000-bbbb", runs)
    assert section["lid"]["aggregates"][0]["provider"] == "p"
    json.dumps(section)


def test_hinted_run_lookup_ignores_detect_runs(runs):
    assert find_hinted_runs("20261002-0000-bbbb", "ja", {"p"}, runs) == {"p": "20261001-0000-aaaa"}
    assert find_hinted_runs("20261002-0000-bbbb", "ko", {"p"}, runs) == {}


def test_hinted_runs_have_no_lid(runs):
    assert score_lid("20261001-0000-aaaa", runs) is None
    assert export_section("20261001-0000-aaaa", runs) is None


def test_detect_run_without_scores_still_reports_accuracy(tmp_settings):
    write_run(tmp_settings, "r", [{"provider": "p", "video_id": "c0", "detected_language": "es"}],
              [{"video_id": "c0", "text": "x"}], language="es", options={"detect_language": True})
    agg = pd.read_parquet(score_lid("r", tmp_settings).with_name("lid_aggregates.parquet"))
    assert agg.iloc[0].lid_accuracy == 1.0
    assert pd.isna(agg.iloc[0].error_rate_detect)
