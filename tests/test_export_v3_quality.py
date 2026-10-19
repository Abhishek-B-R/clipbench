from __future__ import annotations

import json

import httpx

from clipbench.scoring.export_v3_quality import export_section
from clipbench.scoring.score import score_run
from tests.quality_helpers import write_run
from tests.test_judge import Recorder

TEXT = "Um, I paid $5 for Notion, okay?"


def test_export_section_has_all_keys_and_no_text(tmp_settings):
    clips = [{"text": TEXT, "punctuated": True, "itn_spans": [{"start": 11, "end": 13, "kind": "currency"}],
              "entity_spans": [{"start": 18, "end": 24, "type": "brand"}]} for _ in range(3)]  # fmt: skip
    write_run(tmp_settings, "ex", "en", clips, {"p": [TEXT] * 3, "q": ["i paid five dollars for motion ok"] * 3})
    empty = export_section("missing-run", tmp_settings)
    assert set(empty) == {"semantic", "formatting", "entities", "style", "lattice"}
    assert all(v is None for v in empty.values())

    score_run("ex", tmp_settings)
    from clipbench.judge.run import judge_run

    judge_run("ex", tmp_settings, transport=httpx.MockTransport(Recorder()))
    sec = export_section("ex", tmp_settings)
    assert all(sec[k] is not None for k in sec)
    assert sec["semantic"]["published"] is False
    assert sec["formatting"]["aggregates"] and sec["entities"]["aggregates"] and sec["style"]["aggregates"]
    lat = [r for r in sec["lattice"]["aggregates"] if r["provider"] == "q" and r["slice_kind"] == "all"][0]
    assert lat["error_rate_lattice"] < lat["error_rate"]  # ok and okay are equivalent
    blob = json.dumps(sec, ensure_ascii=False)
    for word in ("Notion", "paid", "dollars", "motion"):
        assert word not in blob
