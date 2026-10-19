import dataclasses
import json
import shutil
from pathlib import Path

from clipbench.data.vako import classify_track, load_info, scan_language, scan_tracks

INFO = Path(__file__).parent / "fixtures" / "info"
JSON3 = Path(__file__).parent / "fixtures" / "json3"


def info(vid: str):
    summary = load_info(INFO / f"{vid}.info.json")
    assert summary is not None
    return summary


def test_creator_tracks_from_subtitles():
    a = info("AAAAAAAAAAA")
    assert classify_track("ja", a, "ja") == "creator"
    # a named creator track, key ja-<suffix>, lives under subtitles too
    assert classify_track("ja-XyZ12345678", a, "ja") == "creator"


def test_auto_orig_and_unknown_key():
    a = info("AAAAAAAAAAA")
    assert classify_track("ja-orig", a, "ja") == "auto"
    assert classify_track("ja-NotListed1", a, "ja") == "unknown"
    # empty track lists do not count, as in Vako parseInfo
    assert "ko" not in a.automatic_keys
    assert "live_chat" not in a.subtitle_keys


def test_auto_translation_when_spoken_elsewhere():
    b = info("BBBBBBBBBBB")
    assert classify_track("ja", b, "ja") == "auto_translation"
    assert classify_track("en-orig", b, "en") == "auto"
    assert classify_track("en", b, "en") == "auto"


def test_plain_key_without_language_is_auto_when_nothing_spoken_elsewhere():
    c = info("CCCCCCCCCCC")
    assert classify_track("ja", c, "ja") == "auto"


def test_no_info_is_unknown():
    assert classify_track("ja", None, "ja") == "unknown"
    assert classify_track("ja-orig", None, "ja") == "unknown"


def test_scan_reads_ids_starting_with_dash_and_finds_info(tmp_settings, tmp_path):
    src = tmp_path / "vako" / ".vr" / "src" / "ja"
    src.mkdir(parents=True)
    shutil.copy(JSON3 / "creator.json3", src / "AAAAAAAAAAA.ja.json3")
    shutil.copy(INFO / "AAAAAAAAAAA.info.json", src / "AAAAAAAAAAA.info.json")
    shutil.copy(JSON3 / "asr.json3", src / "-BBBBBBBBBB.ja-orig.json3")
    shutil.copy(JSON3 / "asr.json3", src / "CCCCCCCCCCC.ja.json3")
    (src / "notes.txt").write_text("ignored")
    # clipbench-fetched info for the dash id
    tmp_settings.info_dir.mkdir(parents=True)
    data = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    data["id"] = "-BBBBBBBBBB"
    (tmp_settings.info_dir / "-BBBBBBBBBB.info.json").write_text(json.dumps(data))

    assert set(scan_tracks(src)) == {"AAAAAAAAAAA", "-BBBBBBBBBB", "CCCCCCCCCCC"}
    settings = dataclasses.replace(tmp_settings, vako_root=tmp_path / "vako")
    scans = {s.video_id: s for s in scan_language("ja", settings)}
    assert scans["AAAAAAAAAAA"].info_source == "vako"
    assert scans["AAAAAAAAAAA"].tracks[0].kind == "creator"
    assert scans["-BBBBBBBBBB"].info_source == "clipbench"
    assert scans["-BBBBBBBBBB"].tracks[0].kind == "auto"
    assert scans["CCCCCCCCCCC"].info_source is None
    assert scans["CCCCCCCCCCC"].tracks[0].kind == "unknown"
