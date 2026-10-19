from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import types

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from clipbench.models import Reference, Word
from clipbench.review import store
from clipbench.review.server import create_app

IDS = ["vidA_00001", "vidB_00002", "vidC_00003", "vidD_00004"]
CAPTIONS = {vid: f"caption {vid}" for vid in IDS}


@pytest.fixture
def data(tmp_settings, monkeypatch):
    s = tmp_settings
    s.data_dir.mkdir(parents=True)
    # sample_rank deliberately out of row order
    pd.DataFrame(
        {
            "video_id": [IDS[2], IDS[0], IDS[3], IDS[1]],
            "language": ["ja"] * 4,
            "duration_s": [12.5, 30.0, None, 8.0],
            "channel": ["c3", "c1", "c4", "c2"],
            "creator_track_path": ["x"] * 4,
            "sample_rank": [2, 0, 3, 1],
        }
    ).to_parquet(s.sample_path)
    s.audio_dir.mkdir(parents=True)
    wav = bytes(range(256)) * 40
    for vid in IDS:
        (s.audio_dir / f"{vid}.wav").write_bytes(wav)
    (s.audio_dir / "hashes.json").write_text(json.dumps({IDS[0]: {"original": "o", "wav": "hash0"}}))

    def fake_load_reference(video_id, tier, settings=None):
        assert tier == "creator"
        return Reference(
            video_id=video_id,
            language="ja",
            tier="creator",
            text=CAPTIONS[video_id],
            words=[Word(text="caption", start_ms=0, end_ms=500)],
        )

    fake = types.ModuleType("clipbench.data.references")
    fake.load_reference = fake_load_reference
    monkeypatch.setitem(sys.modules, "clipbench.data.references", fake)
    return s, wav


def client(settings, **kw):
    return TestClient(create_app(settings, reviewer="tester", **kw))


def test_queue_order_and_resume(data):
    s, _ = data
    c = client(s)
    q = c.get("/api/queue").json()
    assert [it["video_id"] for it in q["items"]] == IDS
    assert q["total"] == 4 and q["done"] == 0 and q["resume_index"] == 0
    assert c.post(f"/api/clip/{IDS[0]}", json={"text": "x", "tags": []}).status_code == 200
    assert c.post(f"/api/clip/{IDS[1]}/skip").status_code == 200
    q = c.get("/api/queue").json()
    assert q["done"] == 2 and q["reviewed"] == 1 and q["skipped"] == 1
    assert q["resume_index"] == 2
    assert [it["status"] for it in q["items"]] == ["reviewed", "skipped", "todo", "todo"]


def test_index_html_served(data):
    s, _ = data
    r = client(s).get("/")
    assert r.status_code == 200 and "clipbench review" in r.text


def test_save_writes_verified_json(data):
    s, _ = data
    c = client(s)
    clip = c.get(f"/api/clip/{IDS[0]}").json()
    assert clip["original_text"] == CAPTIONS[IDS[0]] and clip["text"] == CAPTIONS[IDS[0]]
    assert clip["youtube_url"] == f"https://www.youtube.com/shorts/{IDS[0]}"
    r = c.post(f"/api/clip/{IDS[0]}", json={"text": "fixed text", "tags": ["noisy", "background_music"]})
    assert r.status_code == 200
    rec = json.loads((s.verified_dir / f"{IDS[0]}.json").read_text(encoding="utf-8"))
    assert set(rec) == {
        "video_id", "language", "text", "original_text", "tags", "reviewer",
        "reviewed_at", "audio_sha256", "skipped",
        "category", "nonspeech_spans", "codeswitch_spans", "punctuated",
    }
    assert rec["video_id"] == IDS[0] and rec["language"] == "ja"
    assert rec["text"] == "fixed text" and rec["original_text"] == CAPTIONS[IDS[0]]
    assert rec["tags"] == ["background_music", "noisy"]  # canonical TAGS order
    assert rec["reviewer"] == "tester" and rec["skipped"] is False
    assert rec["audio_sha256"] == "hash0"
    assert rec["reviewed_at"].endswith("Z") and len(rec["reviewed_at"]) == 20
    assert not list(s.verified_dir.glob("*.tmp"))
    # re-opening loads the saved text and tags
    again = c.get(f"/api/clip/{IDS[0]}").json()
    assert again["text"] == "fixed text" and again["tags"] == ["background_music", "noisy"]
    assert again["status"] == "reviewed"


def test_sha_computed_when_missing_from_hashes(data):
    s, wav = data
    c = client(s)
    c.post(f"/api/clip/{IDS[1]}", json={"text": "t", "tags": []})
    rec = json.loads((s.verified_dir / f"{IDS[1]}.json").read_text())
    assert rec["audio_sha256"] == hashlib.sha256(wav).hexdigest()


def test_unknown_tag_rejected(data):
    s, _ = data
    r = client(s).post(f"/api/clip/{IDS[0]}", json={"text": "t", "tags": ["bogus"]})
    assert r.status_code == 422
    assert not (s.verified_dir / f"{IDS[0]}.json").exists()


def test_skip(data):
    s, _ = data
    c = client(s)
    assert c.post(f"/api/clip/{IDS[2]}/skip").status_code == 200
    rec = json.loads((s.verified_dir / f"{IDS[2]}.json").read_text())
    assert rec["skipped"] is True and rec["text"] == "" and rec["tags"] == []
    assert rec["original_text"] == CAPTIONS[IDS[2]]
    clip = c.get(f"/api/clip/{IDS[2]}").json()
    assert clip["status"] == "skipped" and clip["text"] == CAPTIONS[IDS[2]]


def test_rereview_first_and_only(data):
    s, _ = data
    c = client(s)
    c.post(f"/api/clip/{IDS[3]}", json={"text": "old", "tags": []})
    path = s.data_dir / "rereview.json"
    path.write_text(json.dumps([IDS[3], IDS[1], "notInSample"]))
    later = time.time() + 5  # rereview.json written after the old review
    os.utime(path, (later, later))

    q = client(s).get("/api/queue").json()
    assert [it["video_id"] for it in q["items"]] == [IDS[1], IDS[3], IDS[0], IDS[2]]
    assert [it["flagged"] for it in q["items"]] == [True, True, False, False]
    assert q["items"][1]["status"] == "rereview"  # saved before the flag, so needs another look
    assert q["resume_index"] == 0

    only = client(s, only_rereview=True)
    q = only.get("/api/queue").json()
    assert [it["video_id"] for it in q["items"]] == [IDS[1], IDS[3]]
    assert only.get(f"/api/clip/{IDS[0]}").status_code == 404
    assert only.get(f"/api/clip/{IDS[3]}").json()["text"] == "old"


@pytest.mark.parametrize(
    "bad",
    ["..%2F..%2Fetc%2Fpasswd", "..", "notInQueue", "vidA_00001.json", "%2E%2E"],
)
def test_path_traversal_rejected(data, bad):
    s, _ = data
    c = client(s)
    assert c.get(f"/api/clip/{bad}").status_code == 404
    r = c.get(f"/audio/{bad}")  # "/audio/.." is normalized to "/" by the client
    assert r.status_code == 404 or not r.headers["content-type"].startswith("audio/")
    assert c.post(f"/api/clip/{bad}", json={"text": "x", "tags": []}).status_code in (404, 405)
    assert not s.verified_dir.exists() or not list(s.verified_dir.iterdir())


def test_store_rejects_bad_ids(tmp_settings):
    with pytest.raises(ValueError):
        store.verified_path(tmp_settings, "../x")
    with pytest.raises(ValueError):
        store.audio_path(tmp_settings, "a/b")


def test_audio_range(data):
    s, wav = data
    c = client(s)
    full = c.get(f"/audio/{IDS[0]}")
    assert full.status_code == 200 and full.content == wav
    assert full.headers["accept-ranges"] == "bytes"
    r = c.get(f"/audio/{IDS[0]}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == wav[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(wav)}"


def test_missing_caption_and_audio(data, monkeypatch):
    s, _ = data
    (s.audio_dir / f"{IDS[1]}.wav").unlink()
    fake = sys.modules["clipbench.data.references"]
    monkeypatch.setattr(fake, "load_reference", lambda *a, **k: None)
    c = client(s)
    clip = c.get(f"/api/clip/{IDS[1]}").json()
    assert clip["audio_available"] is False and clip["original_text"] == "" and clip["error"]
    assert c.get(f"/audio/{IDS[1]}").status_code == 404


def test_empty_queue(tmp_settings):
    q = TestClient(create_app(tmp_settings)).get("/api/queue").json()
    assert q["total"] == 0 and q["resume_index"] is None


# ---------- v2: categories, non-speech spans, code-switch spans, punctuated, window ids ----------

WIN = "abc123XYZ_-@12000-45000"
SHORT = "--WBTbAZr7s"
V2_TEXT = {WIN: "今日は meeting があります", SHORT: "hola everyone que tal"}


@pytest.fixture
def v2data(tmp_settings, monkeypatch):
    import numpy as np
    import soundfile

    s = tmp_settings
    s.data_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "video_id": [SHORT, WIN],
            "clip_id": [SHORT, WIN],
            "youtube_id": [SHORT, "abc123XYZ_-"],
            "language": ["es", "ja"],
            "duration_s": [3.0, 33.0],
            "channel": ["c1", "c2"],
            "category": ["vlog_music", None],
            "sample_rank": [1, 0],
        }
    ).to_parquet(s.sample_path)
    s.audio_dir.mkdir(parents=True)
    t = np.arange(16000 * 2) / 16000
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    for vid in (SHORT, WIN):
        soundfile.write(str(s.audio_dir / f"{vid}.wav"), tone, 16000)

    fake = types.ModuleType("clipbench.data.references")
    fake.load_reference = lambda vid, tier, settings=None: Reference(
        video_id=vid, language="ja", tier="creator", text=V2_TEXT[vid]
    )
    monkeypatch.setitem(sys.modules, "clipbench.data.references", fake)
    return s


@pytest.mark.parametrize(
    "cid,ok",
    [
        (WIN, True),
        (SHORT, True),
        ("abc@1-2", True),
        ("abc@1-", False),
        ("abc@x-2", False),
        ("abc@1-2@3-4", False),
        ("../abc", False),
        ("abc.json", False),
        ("a/b", False),
        ("", False),
    ],
)
def test_clip_id_regex(cid, ok):
    assert bool(store.VIDEO_ID_RE.match(cid)) is ok


def test_window_clip_served_and_linked(v2data):
    c = client(v2data)
    q = c.get("/api/queue").json()
    assert [it["video_id"] for it in q["items"]] == [WIN, SHORT]
    assert q["categories"][0] == "talking_head" and "music" in q["nonspeech_kinds"]
    clip = c.get(f"/api/clip/{WIN}").json()  # "@" is fine raw in a path
    assert clip["kind"] == "window" and clip["start_ms"] == 12000 and clip["end_ms"] == 45000
    assert clip["youtube_url"] == "https://www.youtube.com/watch?v=abc123XYZ_-&t=12"
    assert c.get("/api/clip/abc123XYZ_-%4012000-45000").status_code == 200  # encoded "@"
    assert c.get(f"/audio/{WIN}").status_code == 200
    short = c.get(f"/api/clip/{SHORT}").json()
    assert short["kind"] == "short" and short["youtube_url"] == f"https://www.youtube.com/shorts/{SHORT}"
    c.post(f"/api/clip/{WIN}", json={"text": "x"})
    assert (v2data.verified_dir / f"{WIN}.json").exists()


def test_window_seconds_columns(tmp_settings):
    s = tmp_settings
    s.data_dir.mkdir(parents=True)
    pd.DataFrame(
        {"video_id": ["vid@12-45"], "clip_id": ["vid@12-45"], "youtube_id": ["vid"],
         "window_start_s": [12.0], "window_end_s": [45.0], "sample_rank": [0]}
    ).to_parquet(s.sample_path)
    it = store.load_queue(s).items[0]
    assert (it.start_ms, it.end_ms, it.source_id) == (12000, 45000, "vid")


def test_category_prefill_and_save(v2data):
    c = client(v2data)
    clip = c.get(f"/api/clip/{SHORT}").json()
    assert clip["category"] == "vlog_music" and clip["category_prefilled"] is True
    assert c.get(f"/api/clip/{WIN}").json()["category"] is None
    r = c.post(f"/api/clip/{SHORT}", json={"text": "t", "category": "gaming"})
    assert r.status_code == 200
    rec = json.loads((v2data.verified_dir / f"{SHORT}.json").read_text())
    assert rec["category"] == "gaming"
    again = c.get(f"/api/clip/{SHORT}").json()
    assert again["category"] == "gaming" and again["category_prefilled"] is False
    assert c.post(f"/api/clip/{SHORT}", json={"text": "t", "category": "sports"}).status_code == 422


def test_dataset_csv_category(v2data):
    rows = "platform,video_id,start_ms,end_ms,channel_id,category,license_note,source\n"
    rows += "youtube,abc123XYZ_-,12000,45000,ch,gaming,,test\n"
    import dataclasses

    tmp_root = v2data.data_dir.parent
    s = dataclasses.replace(v2data, root=tmp_root)
    (tmp_root / "datasets" / "ja").mkdir(parents=True)
    (tmp_root / "datasets" / "ja" / "clips.csv").write_text(rows)
    items = {it.video_id: it for it in store.load_queue(s).items}
    assert items[WIN].category == "gaming"


def test_nonspeech_spans_saved(v2data):
    c = client(v2data)
    spans = [
        {"start_ms": 1500.4, "end_ms": 9999, "kind": "sfx"},  # clipped to the 2 s WAV
        {"start_ms": 0, "end_ms": 1200, "kind": "music"},
    ]
    r = c.post(f"/api/clip/{SHORT}", json={"text": "t", "nonspeech_spans": spans})
    assert r.status_code == 200
    rec = json.loads((v2data.verified_dir / f"{SHORT}.json").read_text())
    assert rec["nonspeech_spans"] == [
        {"start_ms": 0, "end_ms": 1200, "kind": "music"},
        {"start_ms": 1500, "end_ms": 2000, "kind": "sfx"},
    ]
    assert c.get(f"/api/clip/{SHORT}").json()["nonspeech_spans"] == rec["nonspeech_spans"]
    bad = [{"start_ms": 0, "end_ms": 100, "kind": "speech"}]
    assert c.post(f"/api/clip/{SHORT}", json={"text": "t", "nonspeech_spans": bad}).status_code == 422
    empty = [{"start_ms": 500, "end_ms": 500, "kind": "music"}]
    assert c.post(f"/api/clip/{SHORT}", json={"text": "t", "nonspeech_spans": empty}).status_code == 422


def test_codeswitch_and_punctuated_saved(v2data):
    c = client(v2data)
    text = "Hola everyone, que tal?"
    body = {
        "text": text,
        "codeswitch_spans": [{"start": 5, "end": 13, "lang": "en", "text": "everyone"}],
        "punctuated": True,
    }
    r = c.post(f"/api/clip/{SHORT}", json=body).json()
    assert r["dropped_codeswitch_spans"] == []
    rec = json.loads((v2data.verified_dir / f"{SHORT}.json").read_text())
    assert rec["codeswitch_spans"] == [{"start": 5, "end": 13, "lang": "en"}]
    assert rec["punctuated"] is True
    # old fields are still there
    for key in ("text", "original_text", "tags", "reviewer", "reviewed_at", "audio_sha256", "skipped", "language"):
        assert key in rec
    clip = c.get(f"/api/clip/{SHORT}").json()
    assert clip["codeswitch_spans"] == [{"start": 5, "end": 13, "lang": "en", "text": "everyone"}]
    assert clip["punctuated"] is True
    bad = {"text": text, "codeswitch_spans": [{"start": 0, "end": 4, "lang": "English!"}]}
    assert c.post(f"/api/clip/{SHORT}", json=bad).status_code == 422


def test_codeswitch_reanchored_on_save(v2data):
    c = client(v2data)
    # Offsets are stale (the reviewer added text before the span); the span text is re-found.
    body = {
        "text": "Oh, hola everyone que tal",
        "codeswitch_spans": [
            {"start": 5, "end": 13, "lang": "en", "text": "everyone"},
            {"start": 0, "end": 4, "lang": "en", "text": "gone"},  # no longer in the text
            {"start": 0, "end": 99, "lang": "en"},  # out of range, no anchor text
        ],
    }
    r = c.post(f"/api/clip/{SHORT}", json=body).json()
    assert r["record"]["codeswitch_spans"] == [{"start": 9, "end": 17, "lang": "en"}]
    assert [d.get("text") for d in r["dropped_codeswitch_spans"]] == ["gone", None]


def test_reanchor_span_nearest():
    text = "ok ok ok"
    assert store.reanchor_span(text, "ok", 3) == (3, 5)  # unchanged
    assert store.reanchor_span(text, "ok", 5) in ((3, 5), (6, 8))
    assert store.reanchor_span(text, "ok", 7) == (6, 8)
    assert store.reanchor_span(text, "no", 0) is None
    assert store.reanchor_span(text, "", 0) is None
    kept, dropped = store.clean_codeswitch("abc hello", [{"start": 0, "end": 5, "lang": "en", "text": "hello"}])
    assert kept == [{"start": 4, "end": 9, "lang": "en"}] and dropped == []


def test_skip_has_v2_defaults(v2data):
    c = client(v2data)
    c.post(f"/api/clip/{SHORT}/skip")
    rec = json.loads((v2data.verified_dir / f"{SHORT}.json").read_text())
    assert rec["skipped"] is True and rec["nonspeech_spans"] == [] and rec["codeswitch_spans"] == []
    assert rec["punctuated"] is False and rec["category"] is None


def test_peaks(v2data):
    c = client(v2data)
    p = c.get(f"/api/clip/{SHORT}/peaks?n=100").json()
    assert p["duration_ms"] == 2000 and p["sample_rate"] == 16000
    assert len(p["min"]) == len(p["max"]) == 100
    assert max(p["max"]) == pytest.approx(1.0, abs=0.01)
    assert min(p["min"]) == pytest.approx(-1.0, abs=0.01)
    assert c.get(f"/api/clip/{SHORT}/peaks?n=5").status_code == 422
    assert c.get("/api/clip/nope/peaks").status_code == 404
