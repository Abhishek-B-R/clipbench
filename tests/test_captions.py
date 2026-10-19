from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench.models import Word
from clipbench.scoring.captions import (
    MAX_CUE_MS,
    Cue,
    build_cues,
    cue_cps,
    export_section,
    score_captions,
    score_cues,
    split_lines,
    to_srt,
    to_vtt,
    write_caption_files,
)
from tests.fixtures.provider_runs import words, write_run


def W(text, s, e):
    return Word(text=text, start_ms=s, end_ms=e)


def test_breaks_on_pause_and_sentence_end():
    ws = [W("Hello", 0, 300), W("there.", 300, 600), W("New", 700, 900), W("cue", 900, 1100),
          W("after", 2000, 2200), W("pause", 2200, 2400)]
    cues = build_cues(ws, "en")
    assert [c.lines for c in cues] == [["Hello there."], ["New cue"], ["after pause"]]
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 600)


def test_two_line_limit_and_balanced_split():
    ws = [W(f"word{i:02d}", i * 200, i * 200 + 180) for i in range(30)]
    cues = build_cues(ws, "en")
    for c in cues:
        assert len(c.lines) <= 2
        assert all(len(line) <= 42 for line in c.lines)
    assert len(cues) > 1
    tokens = ["alpha", "beta", "gamma", "delta,", "epsilon", "zeta", "eta", "theta", "iota", "kappa"]
    lines = split_lines(tokens, "en")
    assert lines == ["alpha beta gamma delta,", "epsilon zeta eta theta iota kappa"]
    assert split_lines(["short", "line"], "en") == ["short line"]


def test_max_cue_duration():
    ws = [W("a", i * 1000, i * 1000 + 900) for i in range(10)]  # gaps of 100 ms, no punctuation
    cues = build_cues(ws, "en")
    assert all(c.duration_ms <= MAX_CUE_MS for c in cues)
    assert len(cues) == 2


def test_japanese_joins_without_spaces_and_uses_16_chars():
    ws = [W("今日は", 0, 400), W("いい", 400, 600), W("天気", 600, 900)]
    assert build_cues(ws, "ja")[0].lines == ["今日はいい天気"]
    long = [W("あいうえお", i * 300, i * 300 + 290) for i in range(7)]  # 35 chars
    cues = build_cues(long, "ja")
    assert all(len(line) <= 16 for c in cues for line in c.lines)


def test_srt_and_vtt_format():
    cues = [Cue(0, 1500, ["Hello", "there"]), Cue(3_661_001, 3_662_000, ["x"])]
    srt = to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello\nthere\n\n2\n01:01:01,001")
    vtt = to_vtt(cues)
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nHello\nthere")
    assert to_srt([]) == "" and to_vtt([]) == "WEBVTT\n\n"


def test_cps_counts_without_spaces_for_ko():
    c = Cue(0, 1000, ["안녕 하세요"])
    assert cue_cps(c, "ko") == pytest.approx(5.0)
    assert cue_cps(c, "en") == pytest.approx(6.0)


def test_score_cues_timing_and_readability():
    ref = [Cue(0, 1000, ["a b"]), Cue(2000, 3000, ["c d"])]
    hyp = [Cue(100, 1100, ["a b"]), Cue(2000, 2500, ["c d e f g h i j k l m n o p"])]
    sc = score_cues(ref, hyp, "en")
    assert sc.n_matched == 2
    assert sc.start_error_median_ms == pytest.approx(50)
    assert sc.end_error_median_ms == pytest.approx((100 + 500) / 2)
    assert sc.intersection_ms == 900 + 500
    assert sc.overlap_ratio == pytest.approx(1400 / (2000 + 1500 - 1400))
    assert sc.cps_over_limit == 1  # 27 chars in 0.5 s
    assert sc.cue_count_ratio == 1.0
    empty = score_cues(ref, [], "en")
    assert empty.n_matched == 0 and empty.overlap_ratio == 0.0


def test_run_scoring_export_and_files(tmp_settings):
    ref_words = words(("Hello", 0, 400), ("world.", 400, 900), ("Bye", 2000, 2400))
    refs = [{"video_id": "c1", "text": "Hello world. Bye", "words": ref_words, "conditions": ["music"]},
            {"video_id": "c2", "text": "untimed"}]
    hyps = [
        {"provider": "p", "video_id": "c1", "text": "Hello world. Bye",
         "words": words(("Hello", 50, 400), ("world.", 400, 950), ("Bye", 2000, 2500))},
        {"provider": "p", "video_id": "c2", "text": "untimed", "words": words(("untimed", 0, 500))},
        {"provider": "notimes", "video_id": "c1", "text": "Hello world. Bye"},
    ]
    write_run(tmp_settings, "r1", hyps, refs)
    clips = pd.read_parquet(score_captions("r1", tmp_settings))
    assert clips[["provider", "video_id"]].values.tolist() == [["p", "c1"]]
    row = clips.iloc[0]
    assert row.n_ref_cues == 2 and row.n_hyp_cues == 2
    assert row.start_error_median_ms == pytest.approx(25)
    agg = pd.read_parquet(tmp_settings.results_dir / "r1" / "captions_aggregates.parquet")
    assert set(agg["slice"]) == {"all", "music"}
    section = export_section("r1", tmp_settings)
    blob = json.dumps(section)
    assert "Hello" not in blob and section["captions"]["rule"]["max_lines"] == 2
    root = write_caption_files("r1", tmp_settings)
    assert root.is_relative_to(tmp_settings.data_dir)
    assert (root / "p" / "c1.srt").read_text().startswith("1\n00:00:00,050")
    assert (root / "p" / "c1.vtt").exists() and not (root / "notimes").exists()


def test_no_timed_data_means_no_section(tmp_settings):
    write_run(tmp_settings, "r2", [{"provider": "p", "video_id": "c", "text": "x"}], [{"video_id": "c", "text": "x"}])
    assert score_captions("r2", tmp_settings) is None
    assert export_section("r2", tmp_settings) is None
