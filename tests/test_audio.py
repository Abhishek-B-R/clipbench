import shutil

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from clipbench.data.audio import fetch_audio, wav_seconds
from clipbench.data.manifest import COLUMNS

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def sample(settings, rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["sample_rank"] = range(len(df))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(settings.sample_path, index=False)


def row(vid, **kw):
    r = dict.fromkeys(COLUMNS)
    r.update(video_id=vid, clip_id=vid, source_id=vid.split("@")[0], platform="youtube", language="en",
             clip_source="short", has_creator_track=True, has_auto_track=False)
    r.update(kw)
    return r


def test_window_section_trimmed_checked_and_falls_back(tmp_settings):
    win = {"clip_source": "window", "window_start_s": 10.0, "window_end_s": 14.0}
    sample(tmp_settings, [
        row("aaaaaaaaaaa@10000-14000", **win),  # section fine
        row("bbbbbbbbbbb@10000-14000", **win),  # section too short, whole audio fine
        row("ddddddddddd@10000-14000", **win),  # both too short
        row("ccccccccccc"),
    ])
    calls = []

    def downloader(vid, audio_dir, cookies, **kw):
        calls.append((vid, kw))
        section = "section" in kw
        if vid.startswith("a"):
            seconds = 5.0
        elif vid.startswith("b"):
            seconds = 3.0 if section else 20.0
        elif vid.startswith("d"):
            seconds = 3.0 if section else 11.0
        else:
            seconds = 30.0
        sf.write(audio_dir / f"{vid}.flac", np.zeros(int(44100 * seconds), dtype="int16"), 44100)
        return True, False, ""

    s = fetch_audio(tmp_settings, downloader=downloader, log=lambda m: None, sleep=lambda x: None)
    assert s.ready == 3 and s.failed == 1
    for vid in ("aaaaaaaaaaa@10000-14000", "bbbbbbbbbbb@10000-14000"):
        assert abs(wav_seconds(tmp_settings.audio_dir / f"{vid}.wav") - 4.0) < 0.01
    info = sf.info(str(tmp_settings.audio_dir / "aaaaaaaaaaa@10000-14000.wav"))
    assert info.samplerate == 16000 and info.channels == 1
    assert calls[0][1]["section"] == (10.0, 15.0)  # one extra second, trimmed after
    assert calls[0][1]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert [c[1].get("section") for c in calls if c[0].startswith("b")] == [(10.0, 15.0), None]
    assert s.failures[0][0].startswith("ddddddddddd")
    assert not (tmp_settings.audio_dir / "ddddddddddd@10000-14000.wav").exists()
    assert abs(wav_seconds(tmp_settings.audio_dir / "ccccccccccc.wav") - 30.0) < 0.01
    # resumable: only the failed clip is asked for again
    calls.clear()
    fetch_audio(tmp_settings, downloader=downloader, log=lambda m: None, sleep=lambda x: None)
    assert {c[0] for c in calls} == {"ddddddddddd@10000-14000"}
