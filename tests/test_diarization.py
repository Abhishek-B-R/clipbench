from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench.models import Reference, SpeakerTurn, Word
from clipbench.scoring.diarization import (
    Segment,
    export_section,
    hypothesis_segments,
    optimal_mapping,
    score_clip,
    score_diarization,
)
from tests.fixtures.provider_runs import words, write_run


def turns(*items):
    return [SpeakerTurn(start_ms=s, end_ms=e, speaker=sp) for s, e, sp in items]


def test_perfect_diarization_with_renamed_labels():
    ref = turns((0, 4000, "A"), (4000, 8000, "B"))
    hyp = [Segment(0, 4000, "spk_1"), Segment(4000, 8000, "spk_0")]
    sc = score_clip(ref, hyp)
    assert sc.der == 0.0
    assert sc.mapping == {"A": "spk_1", "B": "spk_0"}
    assert sc.speaker_count_correct


def test_collar_forgives_boundary_error():
    ref = turns((0, 4000, "A"), (4000, 8000, "B"))
    hyp = [Segment(0, 4200, "x"), Segment(4200, 8000, "y")]  # 200 ms late switch
    assert score_clip(ref, hyp).der == 0.0
    assert score_clip(ref, hyp, collar_ms=0).der == pytest.approx(200 / 8000, abs=0.002)


def test_missed_false_alarm_and_confusion():
    ref = turns((0, 4000, "A"), (4000, 8000, "B"))
    # all one speaker: B's time becomes confusion
    one = score_clip(ref, [Segment(0, 8000, "x")], collar_ms=0)
    assert one.confusion_ms == pytest.approx(4000)
    assert one.der == pytest.approx(0.5)
    assert not one.speaker_count_correct
    # nothing said: all missed
    none = score_clip(ref, [], collar_ms=0)
    assert none.missed_ms == pytest.approx(8000) and none.der == pytest.approx(1.0)
    # speech outside the reference: false alarm
    fa = score_clip(turns((0, 1000, "A")), [Segment(0, 2000, "x")], collar_ms=0)
    assert fa.false_alarm_ms == pytest.approx(1000) and fa.der == pytest.approx(1.0)


def test_overlapping_reference_speech():
    ref = turns((0, 2000, "A"), (1000, 2000, "B"))
    hyp = [Segment(0, 2000, "x")]
    sc = score_clip(ref, hyp, collar_ms=0)
    assert sc.scored_ms == pytest.approx(3000)
    assert sc.missed_ms == pytest.approx(1000)  # two speakers, one hypothesised
    assert sc.der == pytest.approx(1 / 3)


def test_hungarian_mapping_maximises_overlap():
    import numpy as np

    overlap = np.array([[5.0, 9.0], [8.0, 1.0]])
    assert sorted(optimal_mapping(overlap)) == [(0, 1), (1, 0)]


def test_hypothesis_segments_join_short_gaps():
    ws = [Word(text="a", start_ms=0, end_ms=300), Word(text="b", start_ms=600, end_ms=900),
          Word(text="c", start_ms=2000, end_ms=2300), Word(text="d", start_ms=2300, end_ms=2600)]
    segs = hypothesis_segments(ws, ["0", "0", "0", None])
    assert [(s.start_ms, s.end_ms, s.speaker) for s in segs] == [(0, 900, "0"), (2000, 2300, "0")]
    assert hypothesis_segments(ws, None) == []


def test_run_scoring_and_export(tmp_settings):
    turns_json = [{"start_ms": 0, "end_ms": 2000, "speaker": "A"}, {"start_ms": 2000, "end_ms": 4000, "speaker": "B"}]
    ws = words(("hi", 0, 1900), ("yo", 2000, 3900))
    refs = [
        {"video_id": "c1", "text": "hi yo", "speaker_turns": turns_json, "conditions": ["multiple_speakers"]},
        {"video_id": "c2", "text": "solo"},  # no turns: not counted
    ]
    hyps = [
        {"provider": "good", "video_id": "c1", "text": "hi yo", "words": ws, "speakers": ["1", "2"]},
        {"provider": "good", "video_id": "c2", "text": "solo", "words": words(("solo", 0, 500)), "speakers": ["1"]},
        {"provider": "mono", "video_id": "c1", "text": "hi yo", "words": ws, "speakers": ["0", "0"]},
        {"provider": "nolabels", "video_id": "c1", "text": "hi yo", "words": ws},
    ]
    write_run(tmp_settings, "r1", hyps, refs, options={"diarize": True})
    path = score_diarization("r1", tmp_settings)
    clips = pd.read_parquet(path)
    assert set(clips["provider"]) == {"good", "mono"}
    assert list(clips["video_id"].unique()) == ["c1"]
    good = clips[clips.provider == "good"].iloc[0]
    assert good.der == pytest.approx(0.0) and good.speaker_count_correct
    mono = clips[clips.provider == "mono"].iloc[0]
    assert mono.der > 0.3 and not mono.speaker_count_correct
    agg = pd.read_parquet(tmp_settings.results_dir / "r1" / "diarization_aggregates.parquet")
    assert set(agg["slice"]) == {"all", "multiple_speakers"}
    assert agg["too_small"].all()
    section = export_section("r1", tmp_settings)
    assert section["diarization"]["method"]["collar_ms"] == 250
    blob = json.dumps(section)
    assert "hi yo" not in blob and '"A"' not in blob


def test_reference_turns_fall_back_to_verified_file(tmp_settings):
    write_run(
        tmp_settings,
        "r2",
        [{"provider": "p", "video_id": "c9", "text": "x", "words": words(("x", 0, 900)), "speakers": ["s"]}],
        [{"video_id": "c9", "text": "x"}],
    )
    # drop the snapshot columns so the loader reads data/verified
    refs_path = tmp_settings.runs_text_dir / "r2" / "refs.parquet"
    pd.read_parquet(refs_path).drop(columns=["speaker_turns_json", "conditions_json"]).to_parquet(refs_path)
    tmp_settings.verified_dir.mkdir(parents=True, exist_ok=True)
    ref = Reference(video_id="c9", language="en", tier="verified", text="x",
                    speaker_turns=[SpeakerTurn(start_ms=0, end_ms=1000, speaker="A")])
    (tmp_settings.verified_dir / "c9.json").write_text(ref.model_dump_json())
    clips = pd.read_parquet(score_diarization("r2", tmp_settings))
    assert len(clips) == 1


def test_no_data_means_no_section(tmp_settings):
    write_run(tmp_settings, "r3", [{"provider": "p", "video_id": "c", "text": "x"}], [{"video_id": "c", "text": "x"}])
    assert score_diarization("r3", tmp_settings) is None
    assert export_section("r3", tmp_settings) is None
    assert export_section("missing-run", tmp_settings) is None
