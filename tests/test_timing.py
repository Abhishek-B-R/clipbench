from __future__ import annotations

import pytest

from clipbench.models import NonSpeechSpan, Word
from clipbench.scoring.normalize import metrics_for
from clipbench.scoring.timing import (
    nonspeech_hallucination,
    nonspeech_intervals,
    timed_tokens,
    timing_error,
)

EN = metrics_for("en")[0].tokenizer
JA = metrics_for("ja")[0].tokenizer


def W(text, start, end):
    return Word(text=text, start_ms=start, end_ms=end)


def test_timed_tokens_spread_over_word():
    t = timed_tokens([W("あいう", 0, 300), W("え", 300, 400)], JA)
    assert t.tokens == ["あ", "い", "う", "え"]
    assert t.starts == [0, 100, 200, 300]
    assert t.word_index == [0, 0, 0, 1]
    assert t.first_token == {0: 0, 1: 3}


def test_timing_error_words():
    ref = [W("Hello,", 0, 400), W("big", 500, 800), W("world", 1000, 1500)]
    hyp = [W("hello", 100, 400), W("bag", 500, 800), W("world", 1300, 1600)]
    te = timing_error(ref, hyp, EN)
    # 'big' is a substitution and is not counted; offsets 100 and 300
    assert te.n_words == 2
    assert te.median_ms == pytest.approx(200)
    assert te.p90_ms == pytest.approx(280)
    assert te.within_200ms == 0.5


def test_timing_error_characters_map_back_to_words():
    # reference words 東京 / に / 行く; provider gives one long word for the first two
    ref = [W("東京", 1000, 1400), W("に", 1400, 1500), W("行く", 1600, 2000)]
    hyp = [W("東京に", 1000, 1600), W("行く", 1700, 2000)]
    te = timing_error(ref, hyp, JA)
    # に is the 3rd char of a 600 ms word: estimated start 1400 -> offset 0. 行く: offset 100.
    assert te.n_words == 3
    assert te.median_ms == 0
    assert te.within_200ms == 1.0


def test_timing_error_null_without_timings():
    assert timing_error(None, [W("a", 0, 1)], EN) is None
    assert timing_error([W("a", 0, 1)], [], EN) is None


def test_timing_error_no_matches():
    te = timing_error([W("a", 0, 100)], [W("b", 0, 100)], EN)
    assert te.n_words == 0 and te.median_ms is None


def test_nonspeech_from_gaps_with_lead_and_tail():
    ref = [W("a", 1500, 2000), W("b", 2500, 3000), W("c", 5000, 5500)]
    iv = nonspeech_intervals([], ref, duration_ms=8000)
    assert iv == [(0, 1500), (3000, 5000), (5500, 8000)]
    # short lead, no duration: no tail
    assert nonspeech_intervals([], [W("a", 500, 900)], None) == []


def test_nonspeech_tagged_spans_win():
    spans = [NonSpeechSpan(start_ms=0, end_ms=2000, kind="music"), NonSpeechSpan(start_ms=1500, end_ms=3000)]
    assert nonspeech_intervals(spans, [W("a", 0, 100)], 10000) == [(0, 3000)]
    assert nonspeech_intervals([], None, 10000) is None


def test_nonspeech_hallucination_per_minute():
    iv = [(0, 30000), (40000, 70000)]  # one minute of non-speech
    hyp = [W("la", 1000, 2000), W("speech", 32000, 33000), W("la", 50000, 51000), W("la", 69000, 72000)]
    nh = nonspeech_hallucination(iv, hyp)
    # midpoints 1500, 32500, 50500, 70500 -> 2 inside
    assert nh.nonspeech_minutes == pytest.approx(1.0)
    assert nh.words == 2
    assert nh.per_minute == pytest.approx(2.0)


def test_nonspeech_hallucination_nulls():
    assert nonspeech_hallucination(None, [W("a", 0, 1)]).nonspeech_minutes is None
    nh = nonspeech_hallucination([(0, 60000)], None)
    assert nh.nonspeech_minutes == 1.0 and nh.words is None
    assert nonspeech_hallucination([], [W("a", 0, 1)]).per_minute is None
