from __future__ import annotations

import json

import pandas as pd

from clipbench.models import ItnSpan
from clipbench.scoring.formatting import (
    itn_accuracy,
    mark_class,
    punctuation_error,
    score_formatting,
    shape,
    units,
)
from clipbench.scoring.quality_io import load_run
from tests.quality_helpers import write_run


def test_mark_classes_and_priority():
    assert mark_class("?!") == "question"
    assert mark_class("。") == "period"
    assert mark_class("、") == "comma"
    assert mark_class("！") == "exclamation"  # full width under NFKC
    assert mark_class("\"") is None


def test_units_keep_offsets():
    text = "Hi, [music] I paid $5."
    us = units(text, "en")
    assert [u.raw for u in us] == ["Hi", "I", "paid", "5"]
    assert text[us[3].piece_start : us[3].piece_end] == "$5."
    assert us[0].mark == "comma" and us[3].mark == "period"


def test_punctuation_error_rate_counts():
    p = punctuation_error("Hello, world. How are you?", "Hello world! How are you?", "en")
    # comma deleted, period substituted by exclamation, question kept
    assert (p.ref_marks, p.subs, p.dels, p.ins) == (3, 1, 1, 0)
    assert p.error_rate == 2 / 3
    p = punctuation_error("はい。そうです", "はい、そうです。", "ja")
    assert (p.ref_marks, p.subs, p.dels, p.ins) == (1, 1, 0, 1)


def test_shape_signatures():
    assert shape("$1,000") == "$9,9"
    assert shape("March 3, 2024") == "a9,9"
    assert shape("three") == "a"
    assert shape("50%") == "9%"


def test_itn_form_style_value():
    ref = "It costs $5 on 3/14 today."
    spans = [ItnSpan(start=9, end=11, kind="currency"), ItnSpan(start=15, end=19, kind="date")]
    res = itn_accuracy(ref, "It costs five dollars on March 14 today.", spans, "en")
    assert [r.found for r in res] == [True, True]
    assert not res[0].form_match and not res[0].style_match
    assert not res[1].form_match
    res = itn_accuracy(ref, "it costs $5 on 3/14 today", spans, "en")
    assert all(r.form_match and r.style_match and r.value_match for r in res)
    res = itn_accuracy(ref, "It costs $5.00 on 3-14 today.", spans, "en")
    assert res[0].value_match and not res[0].form_match
    assert res[1].value_match and not res[1].style_match


def test_itn_deleted_span_not_found():
    ref = "we have 20 cats"
    res = itn_accuracy(ref, "we have cats", [ItnSpan(start=8, end=10, kind="number")], "en")
    assert not res[0].found and not res[0].form_match


def test_load_run_falls_back_to_verified_file(tmp_settings):
    write_run(tmp_settings, "r1", "en", [{"text": "I have 20 cats."}], {"p": ["I have 20 cats."]})
    refs = pd.read_parquet(tmp_settings.runs_text_dir / "r1" / "refs.parquet").drop(columns=["itn_spans_json"])
    refs.to_parquet(tmp_settings.runs_text_dir / "r1" / "refs.parquet")
    tmp_settings.verified_dir.mkdir(parents=True)
    (tmp_settings.verified_dir / "v00.json").write_text(json.dumps({"itn_spans": [{"start": 7, "end": 9}]}))
    data = load_run("r1", tmp_settings)
    assert data.refs[("v00", "verified")].itn_spans[0].kind == "number"


def test_score_formatting_writes_parquet_and_aggregates(tmp_settings):
    clips = [
        {"text": "I paid $5, okay?", "punctuated": True, "itn_spans": [{"start": 7, "end": 9, "kind": "currency"}],
         "conditions": ["music"]},
        {"text": "Call me at 3 pm.", "punctuated": True, "itn_spans": [{"start": 11, "end": 15, "kind": "time"}]},
        {"text": "no tags here"},
    ]  # fmt: skip
    hyps = {
        "good": ["I paid $5, okay?", "Call me at 3 pm.", "no tags here"],
        "words": ["I paid five dollars okay", "call me at three pm", "no tags here"],
    }
    write_run(tmp_settings, "r2", "en", clips, hyps)
    out = score_formatting("r2", tmp_settings)
    df = pd.read_parquet(out)
    assert len(df) == 4  # the untagged, unpunctuated clip is skipped
    good = df[df.provider == "good"]
    assert (good["punct_error_rate"] == 0).all() and (good["itn_form_acc"] == 1).all()
    words = df[df.provider == "words"]
    assert (words["itn_form_acc"] == 0).all()
    assert words["punct_error_rate"].iloc[0] == 1.0
    agg = pd.read_parquet(out.with_name("formatting_aggregates.parquet"))
    all_good = agg[(agg.provider == "good") & (agg.slice_kind == "all")].iloc[0]
    assert all_good["itn_form_acc"] == 1 and all_good["punct_error_rate"] == 0
    assert {"currency", "time"} <= set(agg[agg.slice_kind == "itn_kind"]["slice"])
    assert "music" in set(agg[agg.slice_kind == "condition"]["slice"])
    assert {"punct_ci_low", "itn_ci_high", "casing_acc"} <= set(agg.columns)
