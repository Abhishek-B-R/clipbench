from __future__ import annotations

import pytest

from clipbench.models import Word
from clipbench.scoring.metrics import align, hallucination
from clipbench.scoring.normalize import metrics_for


def test_identical():
    a = align(["a", "b", "c"], ["a", "b", "c"])
    assert (a.hits, a.subs, a.dels, a.ins) == (3, 0, 0, 0)
    assert a.error_rate == 0


def test_one_substitution():
    a = align(["the", "cat", "sat"], ["the", "bat", "sat"])
    assert (a.hits, a.subs, a.dels, a.ins) == (2, 1, 0, 0)
    assert a.substitutions() == [("cat", "bat")]
    assert a.error_rate == pytest.approx(1 / 3)


def test_deletion_and_insertion():
    # ref: a b c d, hyp: a c d e -> delete b, insert e. 2 errors over 4 ref tokens.
    a = align(["a", "b", "c", "d"], ["a", "c", "d", "e"])
    assert (a.hits, a.subs, a.dels, a.ins) == (3, 0, 1, 1)
    assert a.error_rate == pytest.approx(0.5)
    ops = [(o.op, o.ref, o.hyp, o.ref_index) for o in a.ops]
    assert ops == [
        ("equal", "a", "a", 0),
        ("del", "b", None, 1),
        ("equal", "c", "c", 2),
        ("equal", "d", "d", 3),
        ("ins", None, "e", 4),
    ]


def test_empty_sides():
    a = align([], ["x", "y"])
    assert (a.ins, a.ref_len, a.errors) == (2, 0, 2)
    b = align(["x", "y"], [])
    assert (b.dels, b.error_rate) == (2, 1.0)
    c = align([], [])
    assert c.error_rate == 0


def test_tokens_with_spaces_and_lookalike_ids():
    # tokens are mapped to ids, so these do not collide with the id scheme
    a = align(["w0", "w1", "a b"], ["w1", "w1", "a b"])
    assert (a.hits, a.subs) == (2, 1)


def test_cer_hand_computed_japanese():
    cer = metrics_for("ja")[0]
    # ref こんにちは (5), hyp こんばんは: に->ば, ち->ん; 2 subs.
    a = align(cer.tokenizer("こんにちは。"), cer.tokenizer("こんばんは"))
    assert (a.hits, a.subs, a.dels, a.ins) == (3, 2, 0, 0)
    assert a.error_rate == pytest.approx(0.4)


def test_wer_hand_computed_spanish():
    wer, wer_na = metrics_for("es")
    ref, hyp = "¿Qué tal, amigo? Tengo 3 gatos.", "que tal amigo tengo tres perros"
    a = align(wer.tokenizer(ref), wer.tokenizer(hyp))
    # qué->que, gatos->perros
    assert (a.hits, a.subs, a.dels, a.ins) == (4, 2, 0, 0)
    b = align(wer_na.tokenizer(ref), wer_na.tokenizer(hyp))
    assert (b.hits, b.subs) == (5, 1)


def test_hallucination_length_fallback():
    h = hallucination(10, 20)
    assert h.method == "length"
    assert h.length_ratio == 2.0
    assert h.long_output
    assert h.hallucinated_tokens == 5  # 20 - 1.5 * 10
    assert h.hallucination_rate == 0.5
    ok = hallucination(10, 14)
    assert not ok.long_output and ok.hallucinated_tokens == 0


def test_hallucination_timing():
    ref = [Word(text="a", start_ms=1000, end_ms=1500), Word(text="b", start_ms=4000, end_ms=4500)]
    hyp = [
        Word(text="a", start_ms=1000, end_ms=1500),  # speech
        Word(text="xx yy", start_ms=2500, end_ms=3000),  # midpoint 2750 in the 2.5 s gap: 2 tokens
        Word(text="b", start_ms=4000, end_ms=4500),  # speech
        Word(text="zz", start_ms=6000, end_ms=6500),  # more than 1 s after the last word
        Word(text="q", start_ms=4800, end_ms=5000),  # within 1 s after the last word: not counted
    ]
    h = hallucination(2, 6, ref, hyp)
    assert h.method == "timing"
    assert h.hallucinated_tokens == 3
    assert h.hallucination_rate == 1.5


def test_hallucination_short_gap_not_counted():
    ref = [Word(text="a", start_ms=0, end_ms=500), Word(text="b", start_ms=1200, end_ms=1500)]
    hyp = [Word(text="x", start_ms=700, end_ms=900)]
    assert hallucination(2, 1, ref, hyp).hallucinated_tokens == 0


def test_hallucination_without_hyp_timings_falls_back():
    ref = [Word(text="a", start_ms=0, end_ms=500)]
    assert hallucination(1, 3, ref, None).method == "length"
