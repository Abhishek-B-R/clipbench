"""`clipbench align`: forced alignment of reference text to clip audio, for word timings.

Writes data/align/<clip_id>.json = {"aligner": "<name>@<version>", "words": [{text, start_ms, end_ms}]}
(docs/V2.md). Extra keys: "language", "audio_sha256", "tier".

Method: the MMS 300m forced aligner model (wav2vec2 CTC over romanised characters, 1130 languages)
gives per frame log probabilities; each word is romanised (uroman; Japanese goes through its kana
reading first) and a CTC Viterbi pass finds the best monotonic path. A word spans the frames of its
first to its last character. Needs the optional extra: `uv sync --extra align`. The model weights
(about 1.2 GB) download from Hugging Face on first use, or set CLIPBENCH_ALIGN_MODEL to a local copy.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import typer

from clipbench.config import Settings, get_settings
from clipbench.models import Word
from clipbench.scoring import normalize as norm

log = logging.getLogger(__name__)

MODEL_ID = "MahmoudAshraf/mms-300m-1130-forced-aligner"
MODEL_REVISION = "49402e9577b1158620820667c218cd494cc44486"  # pinned Hugging Face commit
ALIGNER_NAME = "mms-300m-1130-forced-aligner"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 30
ISO3 = {"en": "eng", "ja": "jpn", "ko": "kor", "es": "spa", "hi": "hin"}
REQUIRED = ("torch", "transformers", "uroman")


def align_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir / "align"


def availability() -> tuple[bool, str]:
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        return False, f"missing {', '.join(missing)}; install the extra with `uv sync --extra align`"
    return True, "ok"


# ---------------------------------------------------------------------------
# Text side: words and their romanisation
# ---------------------------------------------------------------------------


_UROMAN = None


def _uroman():
    global _UROMAN
    if _UROMAN is None:
        import uroman

        _UROMAN = uroman.Uroman()
    return _UROMAN


def romanize(text: str, language: str) -> str:
    """uroman romanisation, lowercased, keeping only a-z and apostrophes (the aligner's alphabet)."""
    roman = _uroman().romanize_string(text, lcode=ISO3.get(language)).lower()
    return re.sub(r"[^a-z']", "", roman)


def _speakable(word: str, language: str) -> str:
    """The word as it is said, before romanisation: digits spelled out where a converter exists."""
    if language == "en":
        return norm.english_numbers_to_words(word)
    if language == "es":
        return norm.spanish_numbers_to_words(word)
    if language == "hi":
        return norm.devanagari_digits_to_ascii(word)
    return word


def alignment_words(text: str, language: str) -> list[tuple[str, str]]:
    """(surface, romanised) per word of the reference. Bracket tags are not speech and are dropped.

    Japanese words are fugashi (unidic-lite) tokens and are romanised from their kana reading.
    Other languages split on whitespace.
    """
    text = norm.strip_bracket_tags(norm.nfkc(text or ""))
    out: list[tuple[str, str]] = []
    if language == "ja":
        for tok in norm._tagger()(text):
            surface = tok.surface
            if not surface or all(norm._is_punct_or_symbol(c) for c in surface):
                continue
            reading = norm.katakana_to_hiragana(norm._token_reading(tok))
            reading = norm.japanese_numbers_to_reading(reading)
            out.append((surface, romanize(reading, language)))
        return out
    for word in text.split():
        if all(norm._is_punct_or_symbol(c) for c in word):
            continue
        out.append((word, romanize(_speakable(word, language), language)))
    return out


# ---------------------------------------------------------------------------
# CTC Viterbi
# ---------------------------------------------------------------------------


def ctc_viterbi(log_probs: np.ndarray, targets: list[int], blank: int) -> list[tuple[int, int]]:
    """Best CTC path of `targets` through `log_probs` (frames x vocab).

    Returns (first_frame, last_frame) per target token. Raises ValueError if the audio is too short.
    """
    n_frames = log_probs.shape[0]
    ext = [blank]
    for t in targets:
        ext += [t, blank]
    ext_arr = np.asarray(ext)
    n_states = len(ext)
    allow_skip = np.zeros(n_states, dtype=bool)
    for s in range(2, n_states):
        allow_skip[s] = ext[s] != blank and ext[s] != ext[s - 2]
    repeats = sum(1 for a, b in zip(targets, targets[1:], strict=False) if a == b)
    if n_frames < len(targets) + repeats:
        raise ValueError(f"{n_frames} frames cannot hold {len(targets)} tokens")

    neg = -np.inf
    dp = np.full(n_states, neg)
    dp[0] = log_probs[0, ext[0]]
    if n_states > 1:
        dp[1] = log_probs[0, ext[1]]
    back = np.zeros((n_frames, n_states), dtype=np.int8)
    for t in range(1, n_frames):
        stay = dp
        step = np.concatenate(([neg], dp[:-1]))
        skip = np.concatenate(([neg, neg], dp[:-2]))
        skip = np.where(allow_skip, skip, neg)
        cand = np.stack([stay, step, skip])
        choice = np.argmax(cand, axis=0)
        back[t] = choice
        dp = cand[choice, np.arange(n_states)] + log_probs[t, ext_arr]

    state = n_states - 1 if n_states == 1 or dp[-1] >= dp[-2] else n_states - 2
    path = np.empty(n_frames, dtype=np.int64)
    for t in range(n_frames - 1, -1, -1):
        path[t] = state
        state -= int(back[t, state])

    spans: list[tuple[int, int]] = []
    for k in range(len(targets)):
        frames = np.flatnonzero(path == 2 * k + 1)
        spans.append((int(frames[0]), int(frames[-1])))
    return spans


# ---------------------------------------------------------------------------
# Audio side
# ---------------------------------------------------------------------------


class EmissionModel(Protocol):
    name: str
    vocab: dict[str, int]
    blank: int

    def emissions(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(log_probs frames x vocab, start time in ms of each frame)."""
        ...


class MMSAligner:
    """wav2vec2 CTC emissions from the MMS 300m forced aligner model (transformers)."""

    def __init__(self, model_id: str | None = None) -> None:
        import torch
        from transformers import AutoModelForCTC, AutoTokenizer

        model_id = model_id or os.environ.get("CLIPBENCH_ALIGN_MODEL") or MODEL_ID
        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        rev = {"revision": MODEL_REVISION} if model_id == MODEL_ID else {}
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, **rev)
        self.model = AutoModelForCTC.from_pretrained(model_id, **rev).to(self.device).eval()
        self.vocab = {k.lower(): v for k, v in self.tokenizer.get_vocab().items()}
        # This model has a separate <blank> (0) and <pad> (1); CTC blank is <blank> when present.
        pad = self.model.config.pad_token_id
        self.blank = self.vocab.get("<blank>", pad if pad is not None else 0)
        self.ratio = int(np.prod(self.model.config.conv_stride))
        self.name = f"{ALIGNER_NAME}@{MODEL_REVISION[:10]}"

    def emissions(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        torch = self.torch
        chunk = CHUNK_SECONDS * SAMPLE_RATE
        parts, times = [], []
        for offset in range(0, len(audio), chunk):
            piece = audio[offset : offset + chunk].astype(np.float32)
            if len(piece) < self.ratio * 2:
                continue
            piece = (piece - piece.mean()) / (piece.std() + 1e-7)
            with torch.inference_mode():
                logits = self.model(torch.from_numpy(piece)[None].to(self.device)).logits[0]
                lp = torch.log_softmax(logits.float(), dim=-1).cpu().numpy()
            parts.append(lp)
            times.append((offset + np.arange(lp.shape[0]) * self.ratio) / SAMPLE_RATE * 1000)
        return np.concatenate(parts), np.concatenate(times)


@dataclass
class AlignOutput:
    aligner: str
    words: list[Word]
    skipped_words: int


def align_text(text: str, language: str, audio: np.ndarray, model: EmissionModel) -> AlignOutput:
    """Word timings for `text` in `audio`. Words with no character the model knows are left out."""
    words = alignment_words(text, language)
    targets: list[int] = []
    owners: list[int] = []
    kept: list[int] = []
    for i, (_, roman) in enumerate(words):
        ids = [model.vocab[c] for c in roman if c in model.vocab and model.vocab[c] != model.blank]
        if ids:
            kept.append(i)
            targets += ids
            owners += [i] * len(ids)
    if not targets:
        return AlignOutput(model.name, [], len(words))
    log_probs, frame_ms = model.emissions(audio)
    spans = ctc_viterbi(log_probs, targets, model.blank)
    frame_len = float(frame_ms[1] - frame_ms[0]) if len(frame_ms) > 1 else 20.0
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for (f0, f1), owner in zip(spans, owners, strict=True):
        first.setdefault(owner, f0)
        last[owner] = f1
    out = [
        Word(
            text=words[i][0],
            start_ms=int(round(frame_ms[first[i]])),
            end_ms=int(round(frame_ms[last[i]] + frame_len)),
        )
        for i in kept
    ]
    return AlignOutput(model.name, out, len(words) - len(kept))


def write_alignment(
    clip_id: str, output: AlignOutput, language: str, settings: Settings | None = None, **extra
) -> Path:
    path = align_dir(settings) / f"{clip_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "aligner": output.aligner,
        "words": [w.model_dump() for w in output.words],
        "language": language,
        **extra,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_alignment(clip_id: str, settings: Settings | None = None) -> tuple[str, list[Word]] | None:
    """(aligner, words) from data/align/<clip_id>.json, or None."""
    path = align_dir(settings) / f"{clip_id}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("aligner", "unknown"), [Word.model_validate(w) for w in data.get("words") or []]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_wav(path: Path) -> np.ndarray:
    import soundfile

    audio, sr = soundfile.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"{path} is {sr} Hz, expected {SAMPLE_RATE}")
    return audio


def register(app: typer.Typer) -> None:
    @app.command("align")
    def align_cmd(
        lang: str | None = typer.Option(None, "--lang", help="Only clips in this language."),
        clip: list[str] = typer.Option(None, "--clip", help="Clip id (repeatable). Default: every clip."),
        tier: str = typer.Option("verified", "--tier", help="Reference text to align: verified or creator."),
        force: bool = typer.Option(False, "--force", help="Redo clips that already have an alignment."),
    ) -> None:
        """Forced-align reference text to the clip WAV, writing data/align/<clip_id>.json."""
        import time

        from rich.console import Console

        from clipbench.data.references import load_reference

        console = Console()
        ok, message = availability()
        if not ok:
            console.print(f"clipbench align is not available: {message}")
            raise typer.Exit(1)
        s = get_settings()
        ids = list(clip) if clip else sorted(p.stem for p in s.audio_dir.glob("*.wav"))
        model: MMSAligner | None = None
        done = 0
        for cid in ids:
            out_path = align_dir(s) / f"{cid}.json"
            if out_path.exists() and not force:
                continue
            ref = load_reference(cid, tier, s)  # type: ignore[arg-type]
            wav = s.audio_dir / f"{cid}.wav"
            if ref is None or not wav.is_file():
                console.print(f"{cid}: no {tier} reference or no WAV, skipped")
                continue
            if lang and ref.language != lang:
                continue
            if ref.language not in ISO3:
                console.print(f"{cid}: language {ref.language} not supported, skipped")
                continue
            if model is None:
                console.print(f"loading {MODEL_ID}")
                model = MMSAligner()
            started = time.perf_counter()
            audio = _read_wav(wav)
            try:
                result = align_text(ref.text, ref.language, audio, model)
            except ValueError as exc:
                console.print(f"{cid}: alignment failed: {exc}")
                continue
            write_alignment(cid, result, ref.language, s, tier=tier, audio_sha256=ref.audio_sha256)
            seconds = time.perf_counter() - started
            console.print(
                f"{cid}: {len(result.words)} words ({result.skipped_words} skipped), "
                f"{len(audio) / SAMPLE_RATE:.1f} s audio in {seconds:.1f} s"
            )
            done += 1
        console.print(f"aligned {done} clips into {align_dir(s)}")
