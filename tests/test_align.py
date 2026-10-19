"""Forced alignment, with a fake emission model. No model weights are downloaded."""

from __future__ import annotations

import json

import numpy as np
import pytest

from clipbench import align as al


def test_ctc_viterbi_simple():
    # vocab: 0 blank, 1 a, 2 b. Frames: blank a a blank b blank
    lp = np.log(
        np.array(
            [
                [0.9, 0.05, 0.05],
                [0.1, 0.8, 0.1],
                [0.1, 0.8, 0.1],
                [0.8, 0.1, 0.1],
                [0.1, 0.1, 0.8],
                [0.9, 0.05, 0.05],
            ]
        )
    )
    assert al.ctc_viterbi(lp, [1, 2], blank=0) == [(1, 2), (4, 4)]


def test_ctc_viterbi_repeated_token_needs_blank():
    lp = np.log(np.full((3, 2), 0.5))
    spans = al.ctc_viterbi(lp, [1, 1], blank=0)
    assert spans == [(0, 0), (2, 2)]
    with pytest.raises(ValueError):
        al.ctc_viterbi(np.log(np.full((2, 2), 0.5)), [1, 1], blank=0)


class FakeModel:
    """Emits each target character for 2 frames, with 1 blank frame in between, 20 ms frames."""

    name = "fake@0"
    blank = 0

    def __init__(self, text: str, lead_frames: int = 5) -> None:
        letters = sorted(set(text))
        self.vocab = {c: i + 1 for i, c in enumerate(letters)}
        frames = [0] * lead_frames
        for c in text:
            frames += [self.vocab[c], self.vocab[c], 0]
        lp = np.full((len(frames), len(self.vocab) + 1), np.log(0.01))
        for t, v in enumerate(frames):
            lp[t, v] = np.log(0.9)
        self.lp = lp

    def emissions(self, audio):
        return self.lp, np.arange(self.lp.shape[0]) * 20.0


def test_align_text_english_with_fake_model():
    pytest.importorskip("uroman")
    model = FakeModel("hiyou")
    out = al.align_text("Hi, you! [Music]", "en", np.zeros(16000), model)
    assert [w.text for w in out.words] == ["Hi,", "you!"]
    # lead 5 frames; 'h' frames 5-6, 'i' 8-9 -> word 100..200 ms
    assert (out.words[0].start_ms, out.words[0].end_ms) == (100, 200)
    # 'y' starts at frame 11, 'u' ends at frame 18 -> 220..380 ms
    assert (out.words[1].start_ms, out.words[1].end_ms) == (220, 380)
    assert out.skipped_words == 0


def test_alignment_words_per_language():
    pytest.importorskip("uroman")
    assert al.alignment_words("Tengo 3 gatos.", "es") == [("Tengo", "tengo"), ("3", "tres"), ("gatos.", "gatos")]
    ja = al.alignment_words("東京に行く。", "ja")
    assert [s for s, _ in ja] == ["東京", "に", "行く"]
    assert ja[0][1].startswith("to")
    assert al.alignment_words("안녕 하세요", "ko")[0][1] == "annyeong"
    assert al.alignment_words("नमस्ते।", "hi")[0][1].startswith("namaste")


def test_write_and_load_alignment(tmp_settings):
    from clipbench.models import Word

    out = al.AlignOutput("fake@0", [Word(text="hi", start_ms=0, end_ms=100)], 0)
    path = al.write_alignment("vid@0-1000", out, "en", tmp_settings, tier="verified")
    data = json.loads(path.read_text())
    assert data["aligner"] == "fake@0" and data["words"][0]["text"] == "hi"
    assert path == tmp_settings.data_dir / "align" / "vid@0-1000.json"
    aligner, words = al.load_alignment("vid@0-1000", tmp_settings)
    assert aligner == "fake@0" and words[0].end_ms == 100
    assert al.load_alignment("missing", tmp_settings) is None


def test_cli_reports_missing_extra(monkeypatch):
    import typer
    from typer.testing import CliRunner

    app = typer.Typer()
    al.register(app)
    monkeypatch.setattr(al, "availability", lambda: (False, "missing torch"))
    res = CliRunner().invoke(app, [])
    assert res.exit_code == 1
    assert "not available" in res.output


def test_cli_records_wav_hash_and_redoes_stale_text(tmp_settings, monkeypatch):
    """The alignment records the hash of the WAV it was made from (the verified file may carry none),
    and a reference edited after aligning is aligned again instead of keeping words of the old text."""
    pytest.importorskip("uroman")
    import hashlib

    import soundfile
    import typer
    from typer.testing import CliRunner

    s = tmp_settings
    s.audio_dir.mkdir(parents=True)
    s.verified_dir.mkdir(parents=True)
    wav = s.audio_dir / "-clip1.wav"
    soundfile.write(str(wav), np.zeros(16000, dtype="float32"), 16000)
    wav_sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    ver = s.verified_dir / "-clip1.json"
    ver.write_text(json.dumps({"video_id": "-clip1", "language": "en", "text": "hi you", "tags": []}))

    monkeypatch.setattr(al, "availability", lambda: (True, "ok"))
    monkeypatch.setattr(al, "MMSAligner", lambda: FakeModel("hiyouyo"))
    app = typer.Typer()
    al.register(app)
    res = CliRunner().invoke(app, ["--clip", "-clip1"])
    assert res.exit_code == 0, res.output
    data = json.loads((s.data_dir / "align" / "-clip1.json").read_text())
    assert data["audio_sha256"] == wav_sha
    assert [w["text"] for w in data["words"]] == ["hi", "you"]

    ver.write_text(json.dumps({"video_id": "-clip1", "language": "en", "text": "hi you yo", "tags": []}))
    res = CliRunner().invoke(app, ["--clip", "-clip1"])
    assert res.exit_code == 0, res.output
    data = json.loads((s.data_dir / "align" / "-clip1.json").read_text())
    assert [w["text"] for w in data["words"]] == ["hi", "you", "yo"]
