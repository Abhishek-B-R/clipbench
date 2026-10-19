from __future__ import annotations

import pytest

from clipbench.scoring.readiness import readiness, units


def test_units_latin_attach_marks():
    u = units("Hello, world. ¿Qué tal ? Bien!", "es")
    assert [(x.key, x.raw, x.punct) for x in u] == [
        ("hello", "Hello", "comma"),
        ("world", "world", "final"),
        ("qué", "Qué", None),
        ("tal", "tal", "final"),
        ("bien", "Bien", "final"),
    ]


def test_units_ja_characters():
    u = units("はい、そう。[音楽]", "ja")
    assert [(x.key, x.punct) for x in u] == [("は", None), ("い", "comma"), ("そ", None), ("う", "final")]


def test_units_hindi_danda():
    u = units("मैं ठीक हूँ। ok", "hi")
    assert [x.punct for x in u] == [None, None, "final", None]


def test_readiness_perfect():
    r = readiness("Hi, I am Ana.", "Hi, I am Ana.", "en")
    assert (r.punct_tp, r.punct_fp, r.punct_fn) == (2, 0, 0)
    assert r.punct_f1 == 1.0
    assert r.casing_acc == 1.0
    assert r.casing_n == 4


def test_readiness_hand_computed():
    # ref marks: hi(comma) ana(final). hyp: comma moved to 'i', period kept, casing of 'ana' wrong.
    r = readiness("Hi, I am Ana.", "hi I, am ana.", "en")
    assert (r.punct_tp, r.punct_fp, r.punct_fn) == (1, 1, 1)
    assert r.punct_f1 == pytest.approx(0.5)
    assert (r.casing_n, r.casing_correct) == (4, 2)  # Hi vs hi wrong, I ok, am ok, Ana vs ana wrong


def test_readiness_class_mismatch_and_missing():
    r = readiness("Yes. No.", "Yes, No", "en")
    # Yes: final vs comma -> 1 fp + 1 fn; No: final vs none -> 1 fn
    assert (r.punct_tp, r.punct_fp, r.punct_fn) == (0, 1, 2)
    assert r.punct_f1 == 0.0


def test_readiness_ignores_deleted_words():
    r = readiness("Yes, really. Fine.", "Yes, fine.", "en")
    # really(final) is deleted: not counted. yes comma tp, fine final tp.
    assert (r.punct_tp, r.punct_fp, r.punct_fn) == (2, 0, 0)


def test_readiness_ja_marks():
    r = readiness("はい、そうです。", "はいそうです。", "ja")
    assert (r.punct_tp, r.punct_fp, r.punct_fn) == (1, 0, 1)
    assert r.casing_n == 0 and r.casing_acc is None


def test_readiness_nothing_to_score():
    r = readiness("hola", "hola", "es")
    assert r.punct_f1 is None
