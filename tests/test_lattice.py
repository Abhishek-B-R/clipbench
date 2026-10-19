from __future__ import annotations

import itertools
import json

import pandas as pd
import pytest

from clipbench.models import AlternativeSpan
from clipbench.scoring.lattice import (
    Lattice,
    align_lattice,
    build_lattice,
    kanji_numeral,
    lattice_score,
)
from clipbench.scoring.metrics import align
from clipbench.scoring.normalize import metrics_for
from clipbench.scoring.score import score_run


def spec(lang: str, i: int = 0):
    return metrics_for(lang)[i]


def test_no_alternatives_equals_strict():
    s = spec("es")
    ref, hyp = "hola a todos qué tal", "hola todos que tal estáis"
    la = lattice_score(ref, None, s.tokenizer(hyp), s, "es")
    a = align(s.tokenizer(ref), s.tokenizer(hyp))
    assert (la.hits, la.subs, la.dels, la.ins) == (a.hits, a.subs, a.dels, a.ins)


def test_alternative_span_picks_closest_path():
    s = spec("en")
    ref = "we met at the cafe today"
    alts = [AlternativeSpan(start=14, end=18, options=["café", "coffee shop"])]
    la = lattice_score(ref, alts, s.tokenizer("we met at the coffee shop today"), s, "en")
    assert la.errors == 0
    assert la.path == ["we", "met", "at", "the", "coffee", "shop", "today"]
    strict = align(s.tokenizer(ref), s.tokenizer("we met at the coffee shop today"))
    assert strict.errors == 2


def test_span_is_widened_to_whole_words():
    s = spec("en")
    ref = "it is grey outside"
    # span covers only "re" of grey; the options are spliced back into the word
    alts = [AlternativeSpan(start=7, end=9, options=["ra"])]
    lat = build_lattice(ref, alts, s, "en")
    assert ("gray",) in lat.segments[2]


def test_builtin_english_ok_okay_and_years():
    s = spec("en")
    assert lattice_score("okay let's go", None, s.tokenizer("ok let's go"), s, "en").errors == 0
    assert lattice_score("all right then", None, s.tokenizer("alright then"), s, "en").errors == 0
    # digits are cardinal words after normalisation; the year reading is also valid
    assert lattice_score("back in 1995", None, s.tokenizer("back in nineteen ninety five"), s, "en").errors == 0
    assert lattice_score("in nineteen oh five", None, s.tokenizer("in 1905"), s, "en").errors == 0


def test_builtin_japanese_kanji_kana_and_numerals():
    s = spec("ja")
    assert lattice_score("今日は3時", None, s.tokenizer("きょうは三時"), s, "ja").errors == 0
    assert lattice_score("今日は3時", None, s.tokenizer("キョウはさんじ"), s, "ja").errors == 0
    strict = align(s.tokenizer("今日は3時"), s.tokenizer("きょうは三時"))
    assert strict.errors > 0
    assert kanji_numeral(2024) == "二千二十四"
    assert kanji_numeral(15000) == "一万五千"
    assert kanji_numeral(10) == "十"


def test_builtin_hindi_script_and_spanish_korean_variants():
    s = spec("hi")
    assert lattice_score("क्या हाल है", None, s.tokenizer("kya haal hai"), s, "hi").errors == 0
    es = spec("es")
    assert lattice_score("sólo un vídeo", None, es.tokenizer("solo un video"), es, "es").errors == 0
    ko = spec("ko")
    assert lattice_score("케이크 먹자", None, ko.tokenizer("케익 먹자"), ko, "ko").errors == 0


def test_lattice_never_worse_than_strict():
    s = spec("en")
    ref = "okay so the color of the sky is grey"
    for hyp in ["", "okay", "so the colour", "x y z", "ok so the color of the sky is gray and blue"]:
        la = lattice_score(ref, None, s.tokenizer(hyp), s, "en")
        assert la.errors <= align(s.tokenizer(ref), s.tokenizer(hyp)).errors


def _brute(lat: Lattice, hyp: list[str]) -> int:
    best = None
    for choice in itertools.product(*lat.segments):
        path = [t for p in choice for t in p]
        e = align(path, hyp).errors
        best = e if best is None else min(best, e)
    return best


@pytest.mark.parametrize("hyp", [[], ["a"], ["b", "x"], ["a", "c", "d", "e"], ["z", "z", "z"], ["c", "e"]])
def test_dp_matches_enumeration(hyp):
    lat = Lattice(segments=[[("a",)], [("b",), ("c", "d"), ()], [("e",), ("e", "e")]], strict=["a", "b", "e"])
    res = align_lattice(lat, hyp)
    assert res.errors == _brute(lat, hyp)
    assert res.hits + res.subs + res.ins == len(hyp)


def test_score_run_writes_lattice_columns(tmp_settings):
    run = "lat-run"
    rows, texts, refs = [], [], []
    for i in range(3):
        vid = f"v{i}"
        refs.append(
            {
                "video_id": vid, "tier": "verified", "ref_text": "okay we go to the cafe", "words_json": None,
                "tags": "[]",
                "alternatives_json": json.dumps([{"start": 18, "end": 22, "options": ["coffee shop"]}]),
            }
        )  # fmt: skip
        rows.append(
            {
                "run_id": run, "provider": "p", "model_id": "m", "provider_version": None, "video_id": vid,
                "language": "en", "tier": "verified", "repeat": 0, "status": "ok", "error": None,
                "latency_ms": 10.0, "audio_seconds": 5.0, "cost_usd": 0.0, "cached": False, "duration_s": 5.0,
                "channel": "c", "tags": "[]", "hyp_chars": 10,
            }
        )  # fmt: skip
        texts.append({"provider": "p", "video_id": vid, "repeat": 0, "hyp_text": "ok we go to the coffee shop",
                      "words_json": None})  # fmt: skip
    (tmp_settings.runs_dir / run).mkdir(parents=True)
    (tmp_settings.runs_text_dir / run).mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(tmp_settings.runs_dir / run / "rows.parquet")
    pd.DataFrame(texts).to_parquet(tmp_settings.runs_text_dir / run / "texts.parquet")
    pd.DataFrame(refs).to_parquet(tmp_settings.runs_text_dir / run / "refs.parquet")
    out = score_run(run, tmp_settings)
    scores = pd.read_parquet(out / "scores.parquet")
    assert (scores["error_rate"] > 0).all()
    assert (scores["error_rate_lattice"] == 0).all()
    assert (scores["lattice_ref_len"] == 7).all()
    agg = pd.read_parquet(out / "aggregates.parquet")
    row = agg[agg["slice_kind"] == "all"].iloc[0]
    assert row["error_rate_lattice"] == 0 and row["error_rate"] > 0
    assert {"lattice_ci_low", "lattice_ci_high"} <= set(agg.columns)
