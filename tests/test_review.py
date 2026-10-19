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
