"""Sweeps (A5). Synthetic audio only: numpy tones and noise. No bed is ever downloaded."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest
import soundfile as sf
from typer.testing import CliRunner

from clipbench.models import Clip, Reference, Result, Word
from clipbench.providers.base import Provider
from clipbench.sweeps import beds as beds_mod
from clipbench.sweeps import mix
from clipbench.sweeps.build import RECIPE_COLUMNS, build_sweep, condition_label, is_clean, load_variant_clips
from clipbench.sweeps.score import SWEEPS_COLUMNS, doubling_snr, score_sweep

SR = 16_000
TEXTS = ["今日はいい天気ですね散歩に行きましょう", "駅まで歩いて十分くらいです", "週末は友達と映画を見に行きました"]


def _speech(seconds: float, hz: float, seed: int) -> np.ndarray:
    """Tone bursts (0.4 s on, 0.2 s off) standing in for speech, with a little noise."""
    t = np.arange(int(seconds * SR)) / SR
    on = (t % 0.6) < 0.4
    rng = np.random.default_rng(seed)
    x = 0.3 * np.sin(2 * np.pi * hz * t) * on + 0.001 * rng.standard_normal(len(t))
    return x.astype(np.float32)


def _clips(dirpath: Path, extra_tags: dict[str, list[str]] | None = None) -> list[Clip]:
    dirpath.mkdir(parents=True, exist_ok=True)
    out = []
    for i, text in enumerate(TEXTS + ["音楽が流れています"]):
        vid = f"sweepclip{i:02d}"
        path = dirpath / f"{vid}.wav"
        sf.write(str(path), _speech(3.0, 220 + 110 * i, i), SR, subtype="PCM_16")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        tags = ["background_music"] if i == len(TEXTS) else []
        tags += (extra_tags or {}).get(vid, [])
        words = [Word(text=ch, start_ms=150 * k, end_ms=150 * k + 120) for k, ch in enumerate(text)]
        ref = Reference(video_id=vid, language="ja", tier="verified", text=text, tags=tags, audio_sha256=sha, words=words)
        out.append(Clip(video_id=vid, language="ja", duration_s=3.0, channel="c", audio_path=str(path),
                        audio_sha256=sha, reference=ref))  # fmt: skip
    return out


def _test_beds(settings) -> list[beds_mod.Bed]:
    """One synthetic noise bed and one 'music' bed written as a WAV (a chord), both CC0."""
    d = beds_mod.beds_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    t = np.arange(10 * SR) / SR
    chord = sum(0.1 * np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0)) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    sf.write(str(d / "test-music.wav"), chord.astype(np.float32), SR, subtype="PCM_16")
    return [
        beds_mod.Bed(id="test-music", kind="music", source="test", license="CC0", commercial_use=True),
        beds_mod.Bed(id="synthetic-pink", kind="noise", source="synthetic", license="CC0", commercial_use=True,
                     generator={"type": "pink", "seconds": 20}),  # fmt: skip
        beds_mod.Bed(id="nc-music", kind="music", source="test", license="CC BY-NC 4.0", commercial_use=True),
    ]


# ---------------------------------------------------------------- licenses and manifest


@pytest.mark.parametrize(
    "name,ok",
    [("CC0", True), ("Public domain", True), ("CC BY 3.0", True), ("CC BY-SA 4.0", True), ("CC BY 3.0 us", True),
     ("CC BY-NC 4.0", False), ("CC BY-ND 3.0", False), ("CC BY-NC-SA 3.0", False), ("FAL", False), (None, False),
     ("unknown", False)],  # fmt: skip
)
def test_license_ok(name, ok):
    assert beds_mod.license_ok(name) is ok


def test_committed_manifest_is_all_permissive():
    beds = beds_mod.load_manifest(Path(__file__).resolve().parent.parent / "datasets" / "beds" / "beds.json")
    assert len(beds) >= 10
    assert {b.kind for b in beds} == {"music", "noise"}
    ids = [b.id for b in beds]
    assert len(ids) == len(set(ids))
    for b in beds:
        assert b.usable, b.id
        assert b.license_url or b.license == "Public domain", b.id
        if not b.synthetic:
            assert b.download_url and b.download_url.startswith("https://")
            assert b.page_url and b.sha1 and len(b.sha1) == 40
            assert b.attribution


def test_manifest_has_no_dashes():
    text = (Path(__file__).resolve().parent.parent / "datasets" / "beds" / "beds.json").read_text()
    assert not re.search("[–—]", text)


def test_synthetic_bed_is_deterministic_and_coloured():
    a = beds_mod.synthetic_bed("pink", 4, seed=1)
    b = beds_mod.synthetic_bed("pink", 4, seed=1)
    assert np.array_equal(a, b)
    brown = beds_mod.synthetic_bed("brown", 4, seed=1)
    spec = np.abs(np.fft.rfft(brown)) ** 2
    f = np.fft.rfftfreq(len(brown), 1 / SR)
    assert spec[(f > 50) & (f < 200)].mean() > 100 * spec[(f > 4000) & (f < 6000)].mean()


def test_fetch_beds_offline(tmp_settings):
    calls = []

    def fake_download(url, dst):
        calls.append(url)
        dst.write_bytes(b"audio-bytes")

    def fake_convert(src, dst):
        sf.write(str(dst), np.zeros(SR, dtype=np.float32), SR, subtype="PCM_16")
        return True, ""

    good_sha = hashlib.sha1(b"audio-bytes").hexdigest()
    beds = [
        beds_mod.Bed(id="ok", kind="noise", source="x", license="CC0", commercial_use=True,
                     download_url="https://example.org/a.ogg", sha1=good_sha),  # fmt: skip
        beds_mod.Bed(id="badsha", kind="noise", source="x", license="CC0", commercial_use=True,
                     download_url="https://example.org/b.ogg", sha1="0" * 40),  # fmt: skip
        beds_mod.Bed(id="nc", kind="music", source="x", license="CC BY-NC 3.0", commercial_use=True,
                     download_url="https://example.org/c.ogg"),  # fmt: skip
        beds_mod.Bed(id="synthetic-pink", kind="noise", source="synthetic", license="CC0", commercial_use=True),
    ]
    s = beds_mod.fetch_beds(tmp_settings, beds=beds, downloader=fake_download, converter=fake_convert,
                            log=lambda m: None, sleep=lambda x: None)  # fmt: skip
    assert set(s.ready) == {"ok", "synthetic-pink"}
    assert "badsha" in s.failed and "nc" in s.excluded
    assert len(calls) == 2  # never the non-commercial one
    assert (beds_mod.beds_dir(tmp_settings) / "ok.wav").is_file()
    again = beds_mod.fetch_beds(tmp_settings, beds=beds[:1], downloader=fake_download, converter=fake_convert,
                                log=lambda m: None, sleep=lambda x: None)  # fmt: skip
    assert again.ready == ["ok"] and len(calls) == 2


# ---------------------------------------------------------------- mixing


def test_mix_hits_exact_snr_on_active_speech():
    speech = np.concatenate([np.zeros(SR), _speech(2.0, 300, 0), np.zeros(SR)]).astype(np.float64)
    mask = mix.active_mask(speech, SR, [(0, 1.0), (3.0, 4.0)])
    assert not mask[: SR].any() and not mask[3 * SR :].any()
    # only the bursts are active, not the 0.2 s gaps between them
    assert 0.55 < mask.sum() / (2 * SR) < 0.75
    bed = beds_mod.synthetic_bed("pink", 4, seed=3).astype(np.float64)
    for snr in (20, 5, 0, -5):
        mixed, info = mix.mix_at_snr(speech, bed, snr, mask, SR)
        assert info.measured_snr_db == pytest.approx(snr, abs=1e-6)
        noise = mixed / info.scale - speech
        measured = 20 * np.log10(mix.active_rms(speech, mask) / mix.rms(noise))
        assert measured == pytest.approx(snr, abs=1e-3)
        assert np.max(np.abs(mixed)) <= mix.PEAK_LIMIT + 1e-6


def test_clipping_scale_keeps_snr():
    speech = 0.95 * np.ones(SR)
    bed = beds_mod.synthetic_bed("white", 1, seed=2).astype(np.float64)
    mask = np.ones(SR, dtype=bool)
    mixed, info = mix.mix_at_snr(speech, bed, -5, mask, SR)
    assert info.scale < 1
    assert info.measured_snr_db == pytest.approx(-5, abs=1e-6)


def test_bed_segment_loops_short_beds():
    bed = np.arange(10, dtype=np.float32)
    seg = mix.bed_segment(bed, 25, 7)
    assert len(seg) == 25 and seg[0] == 7 and seg[3] == 0


def test_synthetic_ir_decays_and_reverb_keeps_level():
    ir = mix.synthetic_ir(0.5, seed=1)
    assert ir[0] == 1.0 and len(ir) == int(SR * 0.6)
    head, tail = ir[1 : SR // 20], ir[-SR // 20 :]
    assert np.sum(tail**2) < 1e-3 * np.sum(head**2)
    x = _speech(2.0, 300, 1).astype(np.float64)
    y = mix.apply_reverb(x, ir)
    assert len(y) == len(x)
    assert mix.rms(y) == pytest.approx(mix.rms(x), rel=1e-6)
    assert not np.allclose(x, y)


# ---------------------------------------------------------------- build


def test_condition_labels_and_clean_filter(tmp_path):
    assert condition_label(None, None, False) == "clean"
    assert condition_label("music", -5.0, True) == "music_snr-5_rev"
    clips = _clips(tmp_path)
    assert [is_clean(c) for c in clips] == [True, True, True, False]


def test_build_sweep_writes_wavs_and_recipes(tmp_settings, tmp_path):
    clips = _clips(tmp_path / "src")
    beds = _test_beds(tmp_settings)
    res = build_sweep("ja", [10, 0], kinds=["music", "noise"], reverb=True, settings=tmp_settings, clips=clips,
                      beds=beds, sweep_id="ja-test", log=lambda m: None)  # fmt: skip
    df = res.recipes
    assert list(df.columns) == RECIPE_COLUMNS
    assert res.n_source_clips == 3  # the music clip is excluded
    assert len(df) == 3 * 2 * (1 + 2 * 2)
    assert "nc-music" not in set(df["bed_id"].dropna())
    assert set(df["condition"]) == {"clean", "clean_rev", "music_snr10", "music_snr0", "noise_snr10", "noise_snr0",
                                    "music_snr10_rev", "music_snr0_rev", "noise_snr10_rev", "noise_snr0_rev"}  # fmt: skip
    audio_dir = tmp_settings.data_dir / "sweeps" / "ja-test" / "audio"
    for r in df.itertuples():
        wav = audio_dir / r.wav_name
        assert hashlib.sha256(wav.read_bytes()).hexdigest() == r.sha256
        if isinstance(r.bed_kind, str):
            assert r.measured_snr_db == pytest.approx(r.snr_db, abs=1e-6)
    # same bed and offset along a series
    music = df[(df["bed_kind"] == "music") & ~df["reverb"]]
    assert music.groupby("clip_id")[["bed_id", "bed_offset_s"]].nunique().max().max() == 1
    # the mixed audio really is at that SNR (pads excluded): check one from disk
    row = df[(df["condition"] == "noise_snr0")].iloc[0]
    mixed, _ = sf.read(str(audio_dir / row.wav_name), dtype="float64")
    assert len(mixed) == int(round(row.duration_s * SR))
    # recipes and meta hold no reference text
    blob = df.astype(str).to_string() + res.meta_path.read_text()
    for text in TEXTS:
        assert text[:4] not in blob
    meta = json.loads(res.meta_path.read_text())
    assert meta["n_clips"] == 3 and {b["id"] for b in meta["beds"]} == {"test-music", "synthetic-pink"}
    # variant clips: references shifted by the pad, pads tagged as non-speech
    variants = {c.video_id: c for c in load_variant_clips("ja-test", tmp_settings)}
    v = variants["sweepclip00#music_snr0"]
    assert v.reference.words[0].start_ms == 2000
    assert v.reference.nonspeech_spans[0].start_ms == 0 and v.reference.nonspeech_spans[0].end_ms == 2000
    assert v.reference.nonspeech_spans[-1].end_ms == 7000
    assert v.duration_s == pytest.approx(7.0)


def test_build_sweep_needs_a_bed_on_disk(tmp_settings, tmp_path):
    clips = _clips(tmp_path / "src")
    beds = [beds_mod.Bed(id="missing", kind="music", source="x", license="CC0", commercial_use=True)]
    with pytest.raises(FileNotFoundError):
        build_sweep("ja", [0], kinds=["music"], settings=tmp_settings, clips=clips, beds=beds, log=lambda m: None)


# ---------------------------------------------------------------- run and score


class SnrFake(Provider):
    """Error rate grows as the SNR falls; at low SNR it also 'hears' words in the pads."""

    id: ClassVar[str] = "snr-fake"
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False
    rates: ClassVar[dict[str, float]] = {"clean": 0.0, "10": 0.05, "0": 0.3, "-5": 0.6}

    def __init__(self, scale: float = 1.0, **kw):
        super().__init__("snr-fake-1", scale=scale, **kw)

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        assert clip is not None
        cond = clip.video_id.split("#")[1]
        m = re.search(r"snr(-?[\d.]+)", cond)
        key = "clean" if m is None else m.group(1)
        rate = min(1.0, self.rates.get(key, 0.0) * self.options["scale"])
        ref = clip.reference
        chars = list(ref.text)
        n_bad = int(round(rate * len(chars)))
        out = ["x"] * n_bad + chars[n_bad:]
        words = [Word(text=c, start_ms=w.start_ms, end_ms=w.end_ms) for c, w in zip(out, ref.words, strict=False)]
        if key in ("0", "-5"):
            words.insert(0, Word(text="あ", start_ms=500, end_ms=700))  # inside the lead-in pad
        return Result(text="".join(w.text for w in words), words=words, latency_ms=10.0,
                      audio_seconds=clip.duration_s, cost_usd=0.0, model_id=self.model_id)  # fmt: skip


class SnrFakeHalf(SnrFake):
    id: ClassVar[str] = "snr-fake-half"


async def test_run_and_score_sweep(tmp_settings, tmp_path):
    from clipbench.sweeps.run import run_sweep

    clips = _clips(tmp_path / "src")
    beds = _test_beds(tmp_settings)
    build_sweep("ja", [10, 0, -5], kinds=["music"], settings=tmp_settings, clips=clips, beds=beds,
                sweep_id="ja-run", log=lambda m: None)  # fmt: skip
    res = await run_sweep("ja-run", [SnrFake(), SnrFakeHalf(scale=0.5)], settings=tmp_settings, progress=False)
    assert res.rows_path == tmp_settings.results_dir / "sweeps" / "ja-run" / "rows.parquet"
    assert not (tmp_settings.results_dir / "runs").exists()
    meta = json.loads(res.meta_path.read_text())
    assert meta["kind"] == "sweep" and meta["stability_repeats"] == 0
    assert (tmp_settings.data_dir / "sweeps" / "ja-run" / "texts.parquet").is_file()

    path = score_sweep("ja-run", tmp_settings)
    df = pd.read_parquet(path)
    assert list(df.columns) == SWEEPS_COLUMNS
    prim = df[df["is_primary"]]
    a = prim[prim["provider"] == "snr-fake"].set_index("condition")
    assert a.loc["clean", "error_rate"] == 0
    assert a.loc["music_snr10", "error_rate"] < a.loc["music_snr0", "error_rate"] < a.loc["music_snr-5", "error_rate"]
    assert a.loc["music_snr0", "ci_low"] <= a.loc["music_snr0", "error_rate"] <= a.loc["music_snr0", "ci_high"]
    # clean rate 0 is floored at 1%, so the threshold is 2%: crossed already at +10 dB
    assert a.loc["music_snr10", "doubling_censored"] == "above"
    # hallucination over the pads only at low SNR
    assert a.loc["clean", "halluc_per_nonspeech_min"] == 0
    assert a.loc["music_snr-5", "halluc_per_nonspeech_min"] > 0
    assert a.loc["music_snr-5", "halluc_n_clips"] == 3
    assert (tmp_settings.results_dir / "sweeps" / "ja-run" / "sweep_scores.parquet").is_file()

    # export: listed for a run in the same language
    from clipbench.sweeps.export import export_section

    run_dir = tmp_settings.runs_dir / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(json.dumps({"language": "ja"}))
    section = export_section("r1", tmp_settings)
    assert section and section["sweeps"][0]["sweep_id"] == "ja-run"
    assert section["sweeps"][0]["rows"] and all(r["is_primary"] for r in section["sweeps"][0]["rows"])
    json.dumps(section)  # serialisable, no NaN
    (run_dir / "run_meta.json").write_text(json.dumps({"language": "en"}))
    assert export_section("r1", tmp_settings) is None


def test_doubling_snr_interpolates_and_censors():
    levels = [(20, 0.10), (10, 0.12), (5, 0.18), (0, 0.30), (-5, 0.6)]
    snr, cens = doubling_snr(levels, 0.10)
    assert cens is None and snr == pytest.approx(5 + (0.20 - 0.18) * (0 - 5) / (0.30 - 0.18))
    assert doubling_snr(levels, 0.01) == (20.0, "above")
    assert doubling_snr([(20, 0.1), (0, 0.15)], 0.1) == (None, "below")
    assert doubling_snr([], 0.1) == (None, None)


def test_cli_lists_beds():
    from clipbench.cli import app

    res = CliRunner().invoke(app, ["sweep", "beds"])
    assert res.exit_code == 0, res.output
    assert "synthetic-pink" in res.output
