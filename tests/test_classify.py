import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from clipbench.data.vako import classify_track, load_info, scan_language, scan_tracks

INFO = Path(__file__).parent / "fixtures" / "info"
JSON3 = Path(__file__).parent / "fixtures" / "json3"



@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch):
    """Never touch the committed datasets/ from tests."""
    monkeypatch.setenv("CLIPBENCH_DATASETS_DIR", str(tmp_path / "datasets"))

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


def test_discover_keep_rule():
    from clipbench.data.discover import keep_reason

    a = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    assert keep_reason(a, "ja", set()) is None
    assert keep_reason(a, "ja", {"UCsynthetic000000000001"}) == "blocked channel"
    assert keep_reason(dict(a, duration=300), "ja", set()).startswith("duration")
    b = json.loads((INFO / "BBBBBBBBBBB.info.json").read_text())
    assert keep_reason(b, "ja", set()) == "no creator track"
    en_spoken = dict(a, language="en", automatic_captions={"en-orig": [{}]})
    assert keep_reason(en_spoken, "ja", set()).startswith("not spoken")


def test_discover_loop_with_fakes(tmp_settings, tmp_path):
    from clipbench.data.discover import discover
    from clipbench.data.info import FetchOutcome

    vr = tmp_path / "vako" / ".vr"
    (vr / "src" / "ja").mkdir(parents=True)
    (vr / "vouched-channels.json").write_text(json.dumps(["UCgood", "UCbad"]))
    settings = dataclasses.replace(tmp_settings, vako_root=tmp_path / "vako")
    a = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    b = json.loads((INFO / "BBBBBBBBBBB.info.json").read_text())
    listings = {"UCgood": [f"G{i:010d}" for i in range(5)], "UCbad": [f"B{i:010d}" for i in range(20)]}
    calls = []

    def info_runner(vid, cookies):
        calls.append(vid)
        return FetchOutcome(ok=True, data=dict(a if vid.startswith("G") else b, id=vid))

    def downloader(vid, out_dir, langs, **kw):
        (out_dir / f"{vid}.ja.json3").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{vid}.ja.json3").write_text("{}")
        return True, False, ""

    s = discover(
        settings,
        "ja",
        miss_limit=3,
        lister=lambda url, cookies: (listings[url.split("/channel/")[1].split("/")[0]], False, ""),
        info_runner=info_runner,
        downloader=downloader,
        log=lambda m: None,
        sleep=lambda s: None,
    )
    assert s.kept == 5
    assert len([c for c in calls if c.startswith("B")]) == 3  # dropped after the miss limit
    assert (settings.info_dir / "G0000000000.info.json").exists()
    # resumable: a second run makes no requests
    calls.clear()
    s2 = discover(settings, "ja", lister=lambda *a: (_ for _ in ()).throw(AssertionError()),
                  info_runner=info_runner, downloader=downloader, log=lambda m: None, sleep=lambda s: None)
    assert s2.requests == 0 and calls == []


def test_discover_forgets_a_short_when_tracks_are_blocked(tmp_settings, tmp_path):
    from clipbench.data.discover import discover
    from clipbench.data.info import FetchOutcome

    vr = tmp_path / "vako" / ".vr"
    (vr / "src" / "ja").mkdir(parents=True)
    (vr / "vouched-channels.json").write_text(json.dumps(["UCgood"]))
    settings = dataclasses.replace(tmp_settings, vako_root=tmp_path / "vako")
    a = json.loads((INFO / "AAAAAAAAAAA.info.json").read_text())
    common = dict(
        lister=lambda url, cookies: (["G0000000000"], False, ""),
        info_runner=lambda vid, cookies: FetchOutcome(ok=True, data=dict(a, id=vid)),
        log=lambda m: None,
        sleep=lambda s: None,
    )
    s = discover(settings, "ja", downloader=lambda *a, **k: (False, True, "HTTP Error 429"), **common)
    assert s.stopped and s.kept == 0
    assert not (settings.info_dir / "G0000000000.info.json").exists()

    def ok(vid, out_dir, langs, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{vid}.ja.json3").write_text("{}")
        return True, False, ""

    assert discover(settings, "ja", downloader=ok, **common).kept == 1
