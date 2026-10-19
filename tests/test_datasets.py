import hashlib
import json

import pandas as pd
import pytest

from clipbench.data.check import check_set
from clipbench.data.datasets import (
    ClipRow,
    SpecError,
    clip_id_of,
    clips_path,
    guess_category,
    merge_clips,
    parse_clip_row,
    public_url,
    read_clips,
    split_clip_id,
)
from clipbench.data.importers import import_ids


@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_DATASETS_DIR", str(tmp_path / "datasets"))


def test_clip_ids_and_urls():
    assert clip_id_of("abc", None, None) == "abc"
    assert clip_id_of("-ab", 12000, 40000) == "-ab@12000-40000"
    assert split_clip_id("-ab@12000-40000") == ("-ab", 12000, 40000)
    assert split_clip_id("x@y") == ("x@y", None, None)
    assert public_url("youtube", "abc") == "https://www.youtube.com/shorts/abc"
    assert public_url("youtube", "abc", 12500) == "https://www.youtube.com/watch?v=abc&t=12s"
    assert public_url("tiktok", "123").startswith("https://www.tiktok.com/")


def test_parse_rejects_bad_rows():
    with pytest.raises(SpecError):
        parse_clip_row({"platform": "vimeo", "video_id": "a"})
    with pytest.raises(SpecError):
        parse_clip_row({"video_id": "a", "start_ms": "100"})
    with pytest.raises(SpecError):
        parse_clip_row({"video_id": "a", "start_ms": "100", "end_ms": "50"})
    with pytest.raises(SpecError):
        parse_clip_row({"video_id": "a", "category": "sports"})
    r = parse_clip_row({"video_id": " a ", "category": "gaming"}, default_source="ids")
    assert r.platform == "youtube" and r.video_id == "a" and r.source == "ids" and r.clip_id == "a"


def test_merge_keeps_existing_and_fills_blanks(tmp_settings):
    merge_clips("en", [ClipRow("youtube", "a", source="discover")], tmp_settings)
    res = merge_clips(
        "en",
        [ClipRow("youtube", "a", channel_id="UC1", category="gaming", source="ids"), ClipRow("tiktok", "9")],
        tmp_settings,
    )
    assert res.added == 1 and res.updated == 1
    rows = {r.clip_id: r for r in read_clips("en", tmp_settings)}
    assert rows["a"].source == "discover" and rows["a"].category == "gaming" and rows["a"].channel_id == "UC1"
    text = clips_path("en", tmp_settings).read_text()
    assert text.splitlines()[0] == "platform,video_id,start_ms,end_ms,channel_id,category,license_note,source"


def test_import_ids(tmp_settings, tmp_path):
    p = tmp_path / "ids.csv"
    p.write_text("video_id,start_ms,end_ms,category\nabc,,,comedy_skit\ndef,1000,21000,\n")
    s = import_ids(p, "en", tmp_settings)
    assert s.added == 2
    ids = {r.clip_id for r in read_clips("en", tmp_settings)}
    assert ids == {"abc", "def@1000-21000"}


def test_guess_category():
    assert guess_category({"title": "Easy pasta recipe", "categories": ["Entertainment"]}) == "cooking_tutorial"
    assert guess_category({"title": "x", "categories": ["Gaming"]}) == "gaming"
    assert guess_category({"title": "x", "categories": ["Entertainment"]}) == "talking_head"
    assert guess_category(None) is None


def frame(n_channels: int, per: int, cats: list[str]) -> pd.DataFrame:
    rows = []
    for c in range(n_channels):
        for k in range(per):
            rows.append({"video_id": f"v{c}_{k}", "channel_id": f"UC{c}", "channel": None,
                         "category": cats[c % len(cats)], "category_guess": c % 2 == 0})
    return pd.DataFrame(rows)


CATS = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer"]


def test_check_passes_and_fails():
    ok = check_set(frame(30, 1, CATS))
    assert ok.ok, ok.failures
    few = check_set(frame(10, 1, CATS))
    assert not few.ok and any("channels" in f for f in few.failures)
    heavy = check_set(pd.concat([frame(30, 1, CATS), frame(1, 5, CATS)], ignore_index=True))
    assert not heavy.ok and any("%" in f for f in heavy.failures)
    narrow = check_set(frame(30, 1, CATS[:3] + ["other"]))
    assert not narrow.ok and any("categories" in f for f in narrow.failures)
    strict = check_set(frame(30, 1, CATS), strict=True)  # half the channels only have guesses
    assert not strict.ok


def test_verified_reference_v2_fields(tmp_settings):
    from clipbench.data.manifest import COLUMNS
    from clipbench.data.references import load_reference

    d = tmp_settings.data_dir
    (d / "verified").mkdir(parents=True)
    (d / "align").mkdir(parents=True)
    vid = "abc@12000-40000"
    row = dict.fromkeys(COLUMNS)
    row.update(video_id=vid, clip_id=vid, source_id="abc", platform="youtube", clip_source="window",
               start_ms=12000, end_ms=40000, language="en", category="gaming", has_creator_track=False,
               has_auto_track=False, category_guess=True)
    pd.DataFrame([row], columns=COLUMNS).to_parquet(tmp_settings.manifest_path, index=False)
    (d / "verified" / f"{vid}.json").write_text(json.dumps({
        "video_id": vid, "language": "en", "text": "hello there", "tags": [], "skipped": False,
        "category": "comedy_skit", "punctuated": True,
        "nonspeech_spans": [{"start_ms": 0, "end_ms": 2400, "kind": "music"}],
        "codeswitch_spans": [{"start": 0, "end": 5, "lang": "es"}],
        "conditions": ["music", "fast_speech"],
        "speaker_turns": [{"start_ms": 0, "end_ms": 900, "speaker": "A"}],
        "entity_spans": [{"start": 6, "end": 11, "type": "person"}],
        "alternatives": [{"start": 0, "end": 5, "options": ["helo"]}],
        "itn_spans": [],
    }))
    (d / "audio").mkdir(parents=True)
    (d / "audio" / "hashes.json").write_text(json.dumps({vid: {"original": "o", "wav": "w1"}}))
    align = {"aligner": "mfa@3.1", "words": [{"text": "hello", "start_ms": 0, "end_ms": 300}],
             "tier": "verified", "audio_sha256": "w0", "text_sha256": hashlib.sha256(b"hello there").hexdigest()}
    (d / "align" / f"{vid}.json").write_text(json.dumps(align))
    assert load_reference(vid, "verified", tmp_settings).timing_source is None  # other audio
    align["audio_sha256"] = "w1"
    align["text_sha256"] = "0" * 64
    (d / "align" / f"{vid}.json").write_text(json.dumps(align))
    assert load_reference(vid, "verified", tmp_settings).timing_source is None  # other text
    align["text_sha256"] = hashlib.sha256(b"hello there").hexdigest()
    (d / "align" / f"{vid}.json").write_text(json.dumps(align))
    ref = load_reference(vid, "verified", tmp_settings)
    assert ref is not None
    assert ref.category == "comedy_skit" and ref.punctuated
    assert ref.nonspeech_spans[0].kind == "music" and ref.codeswitch_spans[0].lang == "es"
    assert ref.platform == "youtube" and ref.source_id == "abc" and ref.clip_source == "window"
    assert ref.start_ms == 12000
    assert ref.conditions == ["music", "fast_speech"] and ref.speaker_turns[0].speaker == "A"
    assert ref.entity_spans[0].type == "person" and ref.alternatives[0].options == ["helo"]
    assert ref.itn_spans == []
    assert ref.timing_source == "aligner:mfa@3.1" and ref.words[0].text == "hello"


def test_channels_csv_rejects_whitespace(tmp_path):
    from clipbench.data.datasets import read_channels

    p = tmp_path / "channels.csv"
    p.write_text("platform,channel_id,handle,category,note\nyoutube,,@good,gaming,\n")
    assert read_channels(p)[0].shorts_url() == "https://www.youtube.com/@good/shorts"
    p.write_text("platform,channel_id,handle,category,note\nyoutube,,@bad gaming,,\n")
    with pytest.raises(SpecError):
        read_channels(p)
