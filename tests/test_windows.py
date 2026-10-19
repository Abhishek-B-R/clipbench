import dataclasses
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from clipbench.data.json3 import TimedText, parse_json3_lines, parse_json3_words
from clipbench.data.manifest import build_manifest
from clipbench.data.references import load_reference
from clipbench.data.windows import choose_window, clip_id_for, slice_json3

INFO = Path(__file__).parent / "fixtures" / "info"



@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch):
    """Never touch the committed datasets/ from tests."""
    monkeypatch.setenv("CLIPBENCH_DATASETS_DIR", str(tmp_path / "datasets"))

def line(start_s: float, end_s: float, chars: int) -> TimedText:
    return TimedText(int(start_s * 1000), int(end_s * 1000), "あ" * chars)


def test_window_skips_intro_prefers_density_and_clean_edges():
    lines = [line(0, 9, 200)]  # dense intro, never used
    lines += [line(12 + 6 * k, 17 + 6 * k, 15) for k in range(5)]  # 3 chars/s region
    lines += [line(50.5 + 4 * k, 54.2 + 4 * k, 40) for k in range(8)]  # dense region, 1 s gaps... 0.3 s
    w = choose_window(lines, duration_s=200)
    assert w is not None
    assert w.start_s >= 10
    assert 20 <= w.duration_s <= 60
    assert w.start_s == 50  # dense region wins
    # no line crosses the rounded edges
    for t in lines:
        inside = t.start_ms >= w.start_s * 1000 and t.end_ms <= w.end_s * 1000
        outside = t.end_ms <= w.start_s * 1000 or t.start_ms >= w.end_s * 1000
        assert inside or outside


def test_window_none_when_too_sparse_or_short():
    assert choose_window([line(12, 14, 3), line(40, 42, 3)], 100) is None
    assert choose_window([line(12, 20, 50)], 100) is None  # under 20 s
    assert choose_window([], 100) is None


def test_window_respects_duration():
    lines = [line(12 + 5 * k, 16 + 5 * k, 30) for k in range(10)]
    w = choose_window(lines, duration_s=40)
    assert w is not None and w.end_s <= 40


def asr_track() -> str:
    return json.dumps(
        {
            "events": [
                {"tStartMs": 9000, "dDurationMs": 4000, "segs": [{"utf8": "前"}, {"utf8": "中", "tOffsetMs": 1500}]},
                {"tStartMs": 10, "dDurationMs": 5, "aAppend": 1, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 12000, "dDurationMs": 3000, "segs": [{"utf8": "次"}, {"utf8": "後", "tOffsetMs": 9000}]},
            ]
        }
    )


def test_slice_rebases_and_keeps_words_inside():
    out = slice_json3(asr_track(), 10_000, 20_000)
    words = parse_json3_words(out)
    assert [(w.text, w.start_ms) for w in words] == [("中", 500), ("次", 2000)]
    assert json.loads(out)["clipbenchWindow"] == {"start_ms": 10_000, "end_ms": 20_000}
    assert [t.text for t in parse_json3_lines(out, "ja")] == ["中", "次"]


def test_build_adds_window_rows(tmp_settings, tmp_path):
    src = tmp_path / "vako" / ".vr" / "src" / "ja"
    src.mkdir(parents=True)
    vid = "LLLLLLLLLLL"
    events = [{"tStartMs": 0, "dDurationMs": 5000, "segs": [{"utf8": "はじめに"}]}]
    events += [
        {"tStartMs": 15000 + 4000 * k, "dDurationMs": 3000, "segs": [{"utf8": f"文{k}" + "か" * 20}]} for k in range(10)
    ]
    (src / f"{vid}.ja.json3").write_text(json.dumps({"events": events}, ensure_ascii=False))
    info = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    info.update(id=vid, duration=300)
    (src / f"{vid}.info.json").write_text(json.dumps(info))
    shutil.copy(INFO / "BBBBBBBBBBB.info.json", src / "BBBBBBBBBBB.info.json")
    settings = dataclasses.replace(tmp_settings, vako_root=tmp_path / "vako")
    # a discovered Short in data/tracks with creator + orig tracks
    tracks = settings.data_dir / "tracks"
    tracks.mkdir(parents=True)
    sid = "SSSSSSSSSSS"
    (tracks / f"{sid}.ja.json3").write_text(json.dumps({"events": events[:3]}, ensure_ascii=False))
    (tracks / f"{sid}.ja-orig.json3").write_text(json.dumps({"events": events[:3]}, ensure_ascii=False))
    (tracks / f"{sid}.en.json3").write_text("{}")  # other language, ignored
    settings.info_dir.mkdir(parents=True)
    sinfo = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    sinfo.update(id=sid, duration=30, categories=["Gaming"])
    (settings.info_dir / f"{sid}.info.json").write_text(json.dumps(sinfo))

    from clipbench.data.datasets import ClipRow, merge_clips, read_clips
    from clipbench.data.importers import import_vako

    imp = import_vako("ja", settings)
    assert imp.windows == 1 and imp.shorts == 0
    merge_clips("ja", [ClipRow("youtube", sid, source="discover")], settings)
    assert {r.source for r in read_clips("ja", settings)} == {"vako", "discover"}
    df, _ = build_manifest("ja", settings)
    rows = df.set_index("video_id")
    wins = df[df["clip_source"] == "window"]
    assert len(wins) == 1
    w = wins.iloc[0]
    assert w["youtube_id"] == vid and w["clip_id"] == w["video_id"]
    assert w["video_id"] == clip_id_for(vid, int(w["window_start_s"]), int(w["window_end_s"]))
    assert w["video_id"] == f"{vid}@{int(w['start_ms'])}-{int(w['end_ms'])}" and w["source_id"] == vid
    assert w["window_start_s"] >= 10 and 20 <= w["duration_s"] <= 60
    short = rows.loc[sid]
    assert short["clip_source"] == "short" and short["has_creator_track"] and short["has_auto_track"]
    assert short["auto_track_key"] == "ja-orig"

    ref = load_reference(w["video_id"], "creator", settings)
    assert ref is not None and "はじめに" not in ref.text and ref.text.startswith("文")
    assert ref.clip_source == "window" and ref.source_id == vid and ref.start_ms == int(w["start_ms"])
    assert short["category"] == "gaming" and bool(short["category_guess"])  # guessed, not from clips.csv
    assert pd.notna(w["creator_track_path"]) and "windows" in w["creator_track_path"]
