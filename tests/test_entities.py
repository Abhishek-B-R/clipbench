from __future__ import annotations

import pandas as pd

from clipbench.models import EntitySpan
from clipbench.scoring.entities import (
    entity_scores,
    keyterms_comparison,
    keyterms_on,
    lenient_score,
    score_entities,
)
from tests.quality_helpers import write_run


def test_keyterms_option_parsing():
    assert keyterms_on('{"keyterms": "datasets/en/keyterms.txt"}')
    assert not keyterms_on("{}")
    assert not keyterms_on(None)
    assert not keyterms_on("not json")


def test_entity_exact_lenient_anywhere():
    ref = "I love my new iPhone from Apple."
    spans = [EntitySpan(start=14, end=20, type="product"), EntitySpan(start=26, end=31, type="brand")]
    res = entity_scores(ref, "i love my new i phone from apple", spans, "en")
    assert res[0]["exact"] and res[1]["exact"]  # spaces and case do not matter
    res = entity_scores(ref, "I love my new eye phone from Appel.", spans, "en")
    assert not res[0]["exact"] and 0 < res[0]["lenient"] < 1
    assert not res[1]["exact"] and res[1]["lenient"] == 0.6
    assert lenient_score("abc", "") == 0.0


def test_entity_inside_a_word_and_japanese_and_hindi():
    res = entity_scores("Nike's new shoe", "nike's new shoe", [EntitySpan(start=0, end=4, type="brand")], "en")
    assert res[0]["exact"]
    res = entity_scores("今日はユニクロに行く", "今日はユニクロにいく", [EntitySpan(start=3, end=7, type="brand")], "ja")
    assert res[0]["exact"] and res[0]["anywhere"]
    res = entity_scores("मैं दिल्ली जा रहा हूँ", "main dilli ja raha hun", [EntitySpan(start=4, end=10, type="place")], "hi")
    assert res[0]["exact"]


def test_score_entities_and_keyterms_split(tmp_settings):
    clips = [
        {"text": f"we use Notion and Figma daily {i}", "entity_spans": [
            {"start": 7, "end": 13, "type": "brand"}, {"start": 18, "end": 23, "type": "product"}]}
        for i in range(6)
    ]  # fmt: skip
    hyps = {"p": [f"we use notion and figma daily {i}" for i in range(6)],
            "q": [f"we use motion and fig ma daily {i}" for i in range(6)]}  # fmt: skip
    write_run(tmp_settings, "kt-on", "en", clips, hyps, options={"p": {"keyterms": "k.txt"}, "q": {"keyterms": "k.txt"}})
    write_run(tmp_settings, "kt-off", "en", clips,
              {"p": [f"we use motion and figma daily {i}" for i in range(6)], "q": hyps["q"]})  # fmt: skip
    out = score_entities("kt-on", tmp_settings)
    score_entities("kt-off", tmp_settings)
    df = pd.read_parquet(out)
    assert len(df) == 24 and df["keyterms"].all()
    agg = pd.read_parquet(out.with_name("entities_aggregates.parquet"))
    p_all = agg[(agg.provider == "p") & (agg.slice_kind == "all")].iloc[0]
    assert p_all["exact_acc"] == 1.0 and p_all["n_entities"] == 12
    q_all = agg[(agg.provider == "q") & (agg.slice_kind == "all")].iloc[0]
    assert q_all["exact_acc"] == 0.5  # figma written as "fig ma" still matches with spaces ignored
    assert {"brand", "product"} == set(agg[agg.slice_kind == "entity_type"]["slice"])
    both = pd.concat([df, pd.read_parquet(tmp_settings.results_dir / "kt-off" / "entities.parquet")])
    cmp = keyterms_comparison(both, 7).set_index("provider")
    assert cmp.loc["p", "exact_on"] == 1.0 and cmp.loc["p", "exact_off"] == 0.5
    assert cmp.loc["p", "verdict"] == "a_better"
    assert cmp.loc["q", "diff"] == 0
