import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from clipbench.data.audio import sha256_file
from clipbench.data.manifest import COLUMNS
from clipbench.data.references import load_clips, load_reference

JSON3 = Path(__file__).parent / "fixtures" / "json3"



@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch):
    """Never touch the committed datasets/ from tests."""
    monkeypatch.setenv("CLIPBENCH_DATASETS_DIR", str(tmp_path / "datasets"))

def setup_data(settings, *, duration=12.0):
    d = settings.data_dir
    (d / "audio").mkdir(parents=True)
    (d / "verified").mkdir(parents=True)
    rows = []
    for i, vid in enumerate(["AAAAAAAAAAA", "-BBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD"]):
        rows.append(
            {
                "video_id": vid,
                "language": "ja",
                "duration_s": duration if vid != "CCCCCCCCCCC" else None,
                "channel": f"Synthetic {i}",
                "channel_id": f"UCsyn{i}",
                "has_creator_track": True,
                "has_auto_track": False,
                "creator_track_key": "ja",
                "creator_track_path": str(JSON3 / "creator.json3"),
                "auto_track_key": None,
                "auto_track_path": None,
                "info_path": None,
                "info_source": "clipbench",
            }
        )
    m = pd.DataFrame(rows, columns=COLUMNS)
    m.to_parquet(settings.manifest_path, index=False)
    s = m.copy()
    s["sample_rank"] = range(len(s))
    s.to_parquet(settings.sample_path, index=False)

    hashes = {}
    for vid in ["AAAAAAAAAAA", "-BBBBBBBBBB", "CCCCCCCCCCC"]:  # DDDDDDDDDDD has no audio
        wav = d / "audio" / f"{vid}.wav"
        sf.write(wav, np.zeros(16000 * 2, dtype="int16"), 16000, subtype="PCM_16")
        orig = d / "audio" / f"{vid}.webm"
        orig.write_bytes(b"synthetic")
        hashes[vid] = {"original": sha256_file(orig), "wav": sha256_file(wav)}
    (d / "audio" / "hashes.json").write_text(json.dumps(hashes))
    return hashes


def verified(settings, vid, **kw):
    data = {
        "video_id": vid,
        "language": "ja",
        "text": "直した文",
        "original_text": "x",
        "tags": [],
        "reviewer": "test",
        "reviewed_at": "2026-10-19T12:00:00Z",
        "audio_sha256": None,
        "skipped": False,
    }
    data.update(kw)
    (settings.verified_dir / f"{vid}.json").write_text(json.dumps(data, ensure_ascii=False))


def test_creator_reference(tmp_settings):
    setup_data(tmp_settings)
    ref = load_reference("AAAAAAAAAAA", "creator", tmp_settings)
    assert ref is not None and ref.tier == "creator"
    assert ref.text == "こんにちは皆さん[笑] 今日は晴れです"
    assert ref.words is None
    assert load_reference("ZZZZZZZZZZZ", "creator", tmp_settings) is None


def test_verified_reference_skips_unusable_and_skipped(tmp_settings):
    setup_data(tmp_settings)
    verified(tmp_settings, "AAAAAAAAAAA", tags=["background_music"])
    verified(tmp_settings, "-BBBBBBBBBB", tags=["unusable"])
    verified(tmp_settings, "CCCCCCCCCCC", skipped=True)
    ref = load_reference("AAAAAAAAAAA", "verified", tmp_settings)
    assert ref is not None and ref.text == "直した文" and ref.tags == ["background_music"]
    assert load_reference("-BBBBBBBBBB", "verified", tmp_settings) is None
    assert load_reference("CCCCCCCCCCC", "verified", tmp_settings) is None
    assert load_reference("DDDDDDDDDDD", "verified", tmp_settings) is None


def test_load_clips_creator(tmp_settings):
    hashes = setup_data(tmp_settings)
    clips = load_clips("creator", tmp_settings)
    assert [c.video_id for c in clips] == ["AAAAAAAAAAA", "-BBBBBBBBBB", "CCCCCCCCCCC"]
    assert abs(clips[0].duration_s - 2.0) < 1e-6  # the WAV length wins over the manifest (12 s)
    assert abs(clips[2].duration_s - 2.0) < 1e-6  # from the WAV, manifest had none
    assert clips[0].audio_sha256 == hashes["AAAAAAAAAAA"]["wav"]
    assert clips[0].tier == "creator"
    assert len(load_clips("creator", tmp_settings, limit=2)) == 2


def test_load_clips_verified(tmp_settings):
    hashes = setup_data(tmp_settings)
    verified(tmp_settings, "AAAAAAAAAAA", audio_sha256=hashes["AAAAAAAAAAA"]["wav"])
    verified(tmp_settings, "-BBBBBBBBBB", audio_sha256="0" * 64)  # reviewed against other audio
    verified(tmp_settings, "CCCCCCCCCCC", tags=["unusable"])
    clips = load_clips("verified", tmp_settings)
    assert [c.video_id for c in clips] == ["AAAAAAAAAAA"]
    assert clips[0].tags == []


def test_no_sample_means_no_clips(tmp_settings):
    assert load_clips("creator", tmp_settings) == []
