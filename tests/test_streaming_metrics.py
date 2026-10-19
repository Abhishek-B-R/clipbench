"""Every streaming metric against a hand computed value."""

from __future__ import annotations

import pytest

from clipbench.models import Word
from clipbench.streaming.base import StreamEvent, StreamTranscript
from clipbench.streaming.metrics import clip_metrics, final_text, segment_stability, segments


def ev(t, kind, seg, text, words=None):
    return StreamEvent(t_ms=t, kind=kind, segment=seg, text=text, words=words)


def w(text, start):
    return Word(text=text, start_ms=start, end_ms=start + 200)


def transcript(events, audio_end_ms=2000.0):
    return StreamTranscript(events=events, audio_seconds=audio_end_ms / 1000, chunk_ms=100, speed=1.0,
                            audio_end_ms=audio_end_ms)


EVENTS = [
    ev(200, "partial", "0", "the"),
    ev(400, "partial", "0", "the cat"),
    ev(700, "partial", "0", "a cat sat"),
    ev(1300, "final", "0", "The cat sat.", [w("The", 0), w("cat", 300), w("sat.", 600)]),
    ev(1500, "partial", "1", "on"),
    ev(2600, "final", "1", "on the mat", [w("on", 1000), w("the", 1300), w("mat", 1600)]),
]
REF_WORDS = [w("the", 50), w("cat", 300), w("sat", 900), w("on", 1000), w("the", 1300), w("mat", 1600)]


def test_segment_stability_by_hand():
    # partials "the" | "the cat" | "a cat sat", final "the cat sat":
    # rewritten: only "a" in the third partial -> 1 of 6 partial tokens.
    # revisions: final "the" was shown as the, the, a -> the->a and a->the = 2; cat and sat 0. 2 of 3.
    s = segment_stability([["the"], ["the", "cat"], ["a", "cat", "sat"]], ["the", "cat", "sat"])
    assert (s.partial_tokens, s.rewritten_tokens, s.final_tokens, s.revisions) == (6, 1, 3, 2)
    assert s.rewrite_rate == pytest.approx(1 / 6)
    assert s.revisions_per_word == pytest.approx(2 / 3)


def test_disappearing_word_is_a_revision():
    # "the cat" then "the", final "the cat": cat shown, then absent (1 revision), then final cat (absent -> cat, 0).
    s = segment_stability([["the", "cat"], ["the"]], ["the", "cat"])
    assert s.revisions == 1
    assert s.rewritten_tokens == 0  # both partials survive into the final


def test_clip_metrics_by_hand():
    m = clip_metrics(transcript(EVENTS), "the cat sat on the mat", REF_WORDS, "en")
    assert m.metric == "wer"
    assert m.ttfp_ms == 200
    assert m.ttfs_ms == pytest.approx(600)  # 2600 - 2000
    assert (m.n_partials, m.n_finals, m.n_segments) == (4, 2, 2)
    # seg 0: 6 partial tokens, 1 rewritten; seg 1: "on" vs "on the mat", 1 token, 0 rewritten.
    assert (m.partial_tokens, m.rewritten_tokens) == (7, 1)
    assert m.partial_rewrite_rate == pytest.approx(1 / 7)
    # revisions: seg 0 has 2, seg 1 none; 6 final tokens.
    assert (m.final_tokens, m.revisions) == (6, 2)
    assert m.revisions_per_word == pytest.approx(1 / 3)
    # final text normalises to the reference: no errors (punctuation and casing removed by the en metric).
    assert (m.ref_len, m.errors, m.error_rate) == (6, 0, 0.0)
    # timing offsets: 50, 0, 300, 0, 0, 0 -> median 0, 5 of 6 within 200 ms.
    assert m.timing_n_words == 6
    assert m.timing_median_ms == 0
    assert m.timing_within_200ms == pytest.approx(5 / 6)
    assert m.hyp_chars == len("The cat sat. on the mat")


def test_final_error_rate_uses_batch_alignment():
    m = clip_metrics(transcript(EVENTS), "the cat sat on a mat", None, "en")
    assert (m.subs, m.errors, m.ref_len) == (1, 1, 6)
    assert m.error_rate == pytest.approx(1 / 6)
    assert m.timing_n_words is None  # no reference timings


def test_ttfs_is_zero_when_final_precedes_audio_end_and_ttfp_falls_back_to_final():
    events = [ev(900, "final", "0", "hello")]
    m = clip_metrics(transcript(events, audio_end_ms=1500), "hello", None, "en")
    assert m.ttfs_ms == 0
    assert m.ttfp_ms == 900
    assert m.partial_rewrite_rate is None  # no partials
    assert m.revisions_per_word == 0


def test_no_final_means_no_ttfs_and_all_partials_rewritten():
    events = [ev(300, "partial", "0", "hello there")]
    m = clip_metrics(transcript(events), "hello there", None, "en")
    assert m.ttfs_ms is None
    assert m.partial_rewrite_rate == 1.0
    assert m.error_rate == 1.0  # the final transcript is empty


def test_later_final_replaces_earlier_and_duplicate_partials_count_once():
    events = [
        ev(100, "partial", "a", "hi"),
        ev(150, "partial", "a", "hi"),
        ev(200, "partial", "a", "Hi"),  # same tokens after normalisation
        ev(500, "final", "a", "hi there"),
        ev(900, "final", "a", "Hi there."),  # formatted re-send of the same turn
    ]
    m = clip_metrics(transcript(events, audio_end_ms=600), "hi there", None, "en")
    assert m.partial_tokens == 1
    assert m.ttfs_ms == pytest.approx(300)  # last final 900 - 600
    assert final_text(events) == "Hi there."
    assert m.n_segments == 1


def test_segments_ordered_by_first_final():
    events = [ev(100, "partial", "b", "x"), ev(200, "final", "a", "one"), ev(300, "final", "b", "two")]
    assert [s.key for s in segments(events)] == ["a", "b"]
    assert final_text(events) == "one two"


def test_japanese_uses_character_tokens():
    events = [ev(100, "partial", "0", "今日は"), ev(400, "final", "0", "今日は。")]
    m = clip_metrics(transcript(events, audio_end_ms=300), "今日は", None, "ja")
    assert m.metric == "cer"
    assert (m.partial_tokens, m.final_tokens, m.ref_len, m.errors) == (3, 3, 3, 0)
