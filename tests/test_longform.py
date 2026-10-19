"""Long-form track (A8). Synthetic audio and made-up captions only; no network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from clipbench import longform as lf
from clipbench.models import Clip, Result, Word
from clipbench.providers.base import Provider
from clipbench.scoring.metrics import align

SR = 16_000


def _word(n: int) -> str:
    """A distinct lowercase word per number (letters only, so no normaliser rewrites it)."""
    out = "q"
    for _ in range(3):
        out += "abcdefghijklmnoprstuvwyz"[n % 24]
        n //= 24
    return out


def _lines(n: int, every_ms: int = 15_000, words: int = 6) -> list[Word]:
    """n caption lines of distinct English words, one line every `every_ms`, each 4 s long."""
    out = []
    for i in range(n):
        text = " ".join(_word(i * words + k) for k in range(words))
        out.append(Word(text=text, start_ms=i * every_ms, end_ms=i * every_ms + 4000))
    return out


def _hyp_words(lines: list[Word], drift_ms_per_min: float = 0.0, skip: set[int] | None = None) -> list[Word]:
    out = []
    for i, ln in enumerate(lines):
        if skip and i in skip:
            continue
        shift = int(drift_ms_per_min * ln.start_ms / 60000)
        toks = ln.text.split()
        step = (ln.end_ms - ln.start_ms) // len(toks)
        for k, t in enumerate(toks):
            s = ln.start_ms + k * step + shift
            out.append(Word(text=t, start_ms=s, end_ms=s + step - 10))
    return out


def test_score_video_perfect_and_drifting():
    lines = _lines(40)  # 10 minutes
    text = " ".join(ln.text for ln in lines)
    perfect = lf.score_video(lines, text, _hyp_words(lines), "en", 600.0)
    assert perfect.row["error_rate"] == 0
    assert perfect.row["timing_median_ms"] == 0
    assert perfect.row["n_gaps"] == 0 and perfect.row["dup_events"] == 0
    assert {m["minute"] for m in perfect.minutes} == set(range(10))
    assert sum(m["ref_tokens"] for m in perfect.minutes) == 240

    drift = lf.score_video(lines, text, _hyp_words(lines, drift_ms_per_min=100), "en", 600.0)
    assert drift.row["drift_slope_ms_per_min"] == pytest.approx(100, rel=0.05)
    assert drift.row["abs_slope_ms_per_min"] == pytest.approx(100, rel=0.05)
    assert drift.row["timing_median_after5_ms"] > drift.row["timing_median_first5_ms"]
    late = [m for m in drift.minutes if m["minute"] == 9][0]
    assert late["timing_median_signed_ms"] == pytest.approx(900, abs=60)


def test_score_video_finds_gaps_and_errors_by_minute():
    lines = _lines(40)
    skipped = {8, 9, 10}  # lines at 2:00, 2:15, 2:30 never transcribed: one 34 s gap
    hyp_words = _hyp_words(lines, skip=skipped)
    text = " ".join(w.text for w in hyp_words)
    res = lf.score_video(lines, text, hyp_words, "en", 600.0)
    assert res.row["n_gaps"] == 1
    assert res.row["gap_seconds"] == pytest.approx(34.0)
    assert res.row["longest_gap_s"] == pytest.approx(34.0)
    minute2 = [m for m in res.minutes if m["minute"] == 2][0]
    assert minute2["errors"] == 18 and minute2["ref_tokens"] == 24
    assert res.row["errors"] == 18
    # without word timings, gaps are unknown
    no_words = lf.score_video(lines, text, None, "en", 600.0)
    assert no_words.row["n_gaps"] is None and no_words.row["timing_n_words"] is None


def test_duplications_at_chunk_boundary():
    ref = "a b c d e f g h i j".split()
    hyp = "a b c d e f g h e f g h i j".split()  # "e f g h" written twice
    a = align(ref, hyp)
    events, tokens = lf.duplications(a.ops, hyp, 4)
    assert (events, tokens) == (1, 4)
    # new words that do not repeat earlier text are not duplication
    hyp2 = "a b c d e f x y z q g h i j".split()
    assert lf.duplications(align(ref, hyp2).ops, hyp2, 4) == (0, 0)
    assert lf.dup_min_tokens("ja") == 8 and lf.dup_min_tokens("en") == 4


def test_gaps_ignores_short_or_speechless_stretches():
    lines = [Word(text="a", start_ms=0, end_ms=2000), Word(text="b", start_ms=30_000, end_ms=32_000)]
    hyp = [Word(text="a", start_ms=0, end_ms=2000), Word(text="b", start_ms=30_000, end_ms=32_000)]
    assert lf.gaps(lines, hyp, 40_000) == (0, 0.0, 0.0)  # 28 s without speech is not a gap
    assert lf.gaps(lines, [], 40_000) == (1, 32.0, 32.0)
    assert lf.gaps(lines, None, 40_000) is None


def test_select_spreads_channels():
    cands = pd.DataFrame(
        [{"video_id": f"v{i}", "channel_id": "A" if i < 5 else "B"} for i in range(8)]
    )
    sel = lf.select(cands, 4, seed=1)
    assert len(sel) == 4 and set(sel["channel_id"]) == {"A", "B"}
    assert len(lf.select(cands, None)) == 8


def _json3(lines: list[Word]) -> str:
    events = [{"tStartMs": ln.start_ms, "dDurationMs": ln.end_ms - ln.start_ms, "segs": [{"utf8": ln.text}]}
              for ln in lines]  # fmt: skip
    return json.dumps({"events": events})


def test_candidates_and_fetch(tmp_settings, monkeypatch):
    d = tmp_settings.data_dir
    (d / "info").mkdir(parents=True)
    (d / "tracks").mkdir()
    full = d / "tracks" / "longvid0001.es.json3"
    full.write_text(_json3(_lines(20)))
    rows = []
    for vid, dur in (("longvid0001", 400), ("shortvid001", 100), ("hugevid0001", 3000)):
        info = d / "info" / f"{vid}.info.json"
        info.write_text(json.dumps({"duration": dur}))
        rows.append({"video_id": f"{vid}@20000-60000", "language": "es", "duration_s": 40.0, "channel": "ch",
                     "channel_id": "UC1", "has_creator_track": True, "creator_track_key": "es",
                     "creator_track_path": "window-slice", "info_path": str(info), "source_id": vid,
                     "platform": "youtube", "clip_source": "window", "category": "vlog_music"})  # fmt: skip
    pd.DataFrame(rows).to_parquet(tmp_settings.manifest_path)
    import clipbench.data.vako as vako

    monkeypatch.setattr(vako, "merged_tracks", lambda s, lang: {"longvid0001": {"es": full}, "hugevid0001": {"es": full}})
    cands = lf.candidates("es", tmp_settings)
    assert list(cands["video_id"]) == ["longvid0001"]  # 100 s and 3000 s are out of range
    assert cands.iloc[0]["creator_track_path"] == str(full)

    calls = []

    def fake_download(vid, adir, cookies):
        calls.append(vid)
        (Path(adir) / f"{vid}.webm").write_bytes(b"x")
        return True, False, ""

    def fake_convert(src, dst):
        sf.write(str(dst), np.zeros(SR * 2, dtype=np.float32), SR, subtype="PCM_16")
        return True, ""

    s = lf.fetch("es", 5, tmp_settings, downloader=fake_download, converter=fake_convert, log=lambda m: None,
                 sleep=lambda x: None)  # fmt: skip
    assert s.ready == 1 and calls == ["longvid0001"]
    clips = lf.load_clips("es", tmp_settings)
    assert len(clips) == 1 and clips[0].reference.tier == "creator"
    assert clips[0].reference.words[1].start_ms == 15_000
    again = lf.fetch("es", 5, tmp_settings, downloader=fake_download, converter=fake_convert, log=lambda m: None,
                     sleep=lambda x: None)  # fmt: skip
    assert again.ready == 1 and calls == ["longvid0001"]


class Chunky(Provider):
    """Transcribes perfectly with timings, but repeats one line (a chunk seam) and drifts 50 ms/min."""

    id: ClassVar[str] = "chunky"
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        lines = clip.reference.words
        words = _hyp_words(lines, drift_ms_per_min=50)
        dup = [w for w in words if lines[5].start_ms <= w.start_ms < lines[6].start_ms]
        words = words[:36] + dup + words[36:]
        return Result(text=" ".join(w.text for w in words), words=words, latency_ms=1000.0,
                      audio_seconds=clip.duration_s, cost_usd=0.0, model_id="chunky-1")  # fmt: skip


class Broken(Provider):
    id: ClassVar[str] = "broken"
    paid: ClassVar[bool] = False

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        raise RuntimeError("file too long")


async def test_run_score_and_export(tmp_settings, tmp_path):
    from clipbench.models import Reference

    lines = _lines(40)
    wav = tmp_path / "lv.wav"
    sf.write(str(wav), (0.1 * np.random.default_rng(0).standard_normal(SR * 4)).astype(np.float32), SR)
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    ref = Reference(video_id="lv000000001", language="en", tier="creator", text=" ".join(x.text for x in lines),
                    words=lines, audio_sha256=sha, clip_source="long")  # fmt: skip
    clip = Clip(video_id="lv000000001", language="en", duration_s=600.0, audio_path=str(wav), audio_sha256=sha,
                reference=ref)  # fmt: skip
    chunky = Chunky()
    res = await lf.run_longform("en", [chunky, Broken()], clips=[clip], settings=tmp_settings, run_id="lf-t",
                                progress=False)  # fmt: skip
    assert chunky.timeout_s >= 1200
    assert res.rows_path == tmp_settings.results_dir / "longform" / "lf-t" / "rows.parquet"
    assert not (tmp_settings.results_dir / "runs").exists()
    meta = json.loads(res.meta_path.read_text())
    assert meta["kind"] == "longform" and meta["track"] == "longform"

    path = lf.score_longform("lf-t", tmp_settings)
    scores = pd.read_parquet(path).set_index("provider")
    assert list(pd.read_parquet(path).columns) == lf.LONGFORM_COLUMNS
    assert scores.loc["chunky", "dup_events"] == 1 and scores.loc["chunky", "dup_tokens"] == 6
    assert scores.loc["chunky", "drift_slope_ms_per_min"] == pytest.approx(50, rel=0.1)
    assert scores.loc["broken", "status"] == "error"
    agg = pd.read_parquet(path.parent / "longform_aggregates.parquet")
    assert list(agg.columns) == lf.AGGREGATE_COLUMNS
    top = agg[(agg["slice_kind"] == "all")].set_index("provider")
    assert top.loc["broken", "failure_rate"] == 1.0 and top.loc["chunky", "failure_rate"] == 0.0
    assert top.loc["chunky", "dup_events_per_hour"] == pytest.approx(6.0)
    assert set(agg[agg["slice_kind"] == "minute"]["slice"]) == {str(m) for m in range(10)}
    assert (path.parent / "longform_minutes.parquet").is_file()
    # no text in the published files
    for f in path.parent.glob("*.parquet"):
        assert lines[5].text.split()[0] not in pd.read_parquet(f).astype(str).to_string()

    run_dir = tmp_settings.runs_dir / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(json.dumps({"language": "en"}))
    section = lf.export_section("r1", tmp_settings)
    assert section["longform"]["track"] == "longform" and section["longform"]["run_id"] == "lf-t"
    assert section["longform"]["n_videos"] == 1 and section["longform"]["minutes"]
    json.dumps(section)
    (run_dir / "run_meta.json").write_text(json.dumps({"language": "ja"}))
    assert lf.export_section("r1", tmp_settings) is None
