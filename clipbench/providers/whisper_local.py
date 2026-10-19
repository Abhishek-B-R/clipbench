"""Whisper large-v3 and large-v3-turbo on this machine. Free, nothing leaves the laptop.

Backend: mlx-whisper (Apple silicon GPU) when it imports, else faster-whisper (CTranslate2, int8 on CPU).
mlx imports fail without a Metal device (headless or sandboxed shells), so the backend is decided by an
actual import, not by whether the package is installed. The backend is part of `settings()`, so results
from the two backends never share a cache entry, and it is recorded as `provider_version`.

Decoding, chosen to reduce Whisper's repetition and hallucination loops on short noisy clips:
- `language` is set (no language detection),
- `temperature=0` only (no fallback sampling at higher temperatures),
- `condition_on_previous_text=False` (a bad window cannot seed the next one),
- `word_timestamps=True` for word timings.
mlx-whisper decodes greedily (it has no beam search); faster-whisper uses its default beam size 5.

Run options: detect_language passes language=None (Whisper's own language identification on the
first 30 s) and reads the detected language back; keyterms become `initial_prompt` (the same prompt
string as the hosted Whisper adapters). No diarization.

Weights are downloaded from Hugging Face on first use (about 3 GB for large-v3, 1.6 GB for turbo)
into the Hugging Face cache, so the first clip is slow. Tests never load them.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import Provider, normalize_language_code
from clipbench.providers.groq import keyterm_prompt

_BACKEND: str | None = None
_BACKEND_LOCK = threading.Lock()
_FW_MODELS: dict[str, Any] = {}


def detect_backend() -> str | None:
    """'mlx', 'faster-whisper' or None. Imports lazily and remembers the answer."""
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is not None:
            return _BACKEND or None
        try:
            import mlx_whisper  # noqa: F401

            _BACKEND = "mlx"
        except Exception:
            try:
                import faster_whisper  # noqa: F401

                _BACKEND = "faster-whisper"
            except Exception:
                _BACKEND = ""
        return _BACKEND or None


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class WhisperLocal(Provider):
    id: ClassVar[str] = "whisper-local"
    mlx_model: ClassVar[str] = "mlx-community/whisper-large-v3-mlx"
    fw_model: ClassVar[str] = "large-v3"
    concurrency: ClassVar[int] = 1
    timeout_s: ClassVar[float] = 1800.0  # the first call downloads the weights
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False
    supported_options: ClassVar[frozenset[str]] = frozenset({"detect_language", "keyterms"})

    def __init__(self, model_id: str | None = None, *, backend: str | None = None, **options: Any) -> None:
        self._backend = backend
        super().__init__(model_id, **options)

    @property
    def backend(self) -> str | None:
        if self._backend is None:
            self._backend = detect_backend()
        return self._backend

    def default_model_id(self) -> str:  # type: ignore[override]
        return self.fw_model if self.backend == "faster-whisper" else self.mlx_model

    def availability(self) -> tuple[bool, str]:
        if _installed("mlx_whisper") or _installed("faster_whisper"):
            return True, "ok"
        return False, f"{self.id}: skipped, install the local extra (uv sync --extra local) for mlx-whisper or faster-whisper"

    def settings(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "backend": self.backend,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            **self.options,
        }

    def price_per_minute(self) -> float:
        return 0.0

    def estimate_cost(self, audio_seconds: float) -> float:
        return 0.0

    # ------------------------------------------------------------ backends

    def decode_options(self, language: str) -> dict[str, Any]:
        """language (None asks Whisper to detect it) and initial_prompt for the keyterms."""
        out: dict[str, Any] = {"language": None if self.detect_language else language}
        prompt = keyterm_prompt(self.keyterms)
        if prompt:
            out["initial_prompt"] = prompt
        return out

    def _run_mlx(self, audio_path: Path, language: str) -> tuple[str, list[Word], Any]:
        import mlx_whisper

        out = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=self.model_id,
            **self.decode_options(language),
            word_timestamps=True,
            temperature=0.0,
            condition_on_previous_text=False,
            verbose=None,
        )
        words: list[Word] = []
        for seg in out.get("segments", []):
            for w in seg.get("words", []) or []:
                words.append(
                    Word(text=w["word"].strip(), start_ms=int(w["start"] * 1000), end_ms=int(w["end"] * 1000))
                )
        raw = {"language": out.get("language"), "segments": [
            {k: s.get(k) for k in ("start", "end", "text", "avg_logprob", "no_speech_prob", "compression_ratio")}
            for s in out.get("segments", [])
        ]}
        return str(out.get("text", "")).strip(), words, raw

    def _run_faster_whisper(self, audio_path: Path, language: str) -> tuple[str, list[Word], Any]:
        from faster_whisper import WhisperModel

        model = _FW_MODELS.get(self.model_id)
        if model is None:
            model = WhisperModel(self.model_id, device="cpu", compute_type="int8")
            _FW_MODELS[self.model_id] = model
        segments, info = model.transcribe(
            str(audio_path),
            **self.decode_options(language),
            word_timestamps=True,
            temperature=0.0,
            condition_on_previous_text=False,
        )
        texts: list[str] = []
        words: list[Word] = []
        raw_segs = []
        for seg in segments:
            texts.append(seg.text)
            raw_segs.append({"start": seg.start, "end": seg.end, "text": seg.text,
                             "avg_logprob": seg.avg_logprob, "no_speech_prob": seg.no_speech_prob})
            for w in seg.words or []:
                words.append(Word(text=w.word.strip(), start_ms=int(w.start * 1000), end_ms=int(w.end * 1000)))
        joiner = "" if (info.language if self.detect_language else language) in ("ja", "zh", "th") else " "
        text = joiner.join(t.strip() for t in texts).strip()
        return text, words, {"language": info.language, "segments": raw_segs}

    def _transcribe_sync(self, audio_path: Path, language: str) -> Result:
        backend = self.backend
        if backend is None:
            raise RuntimeError("neither mlx-whisper nor faster-whisper could be imported")
        t0 = time.perf_counter()
        if backend == "mlx":
            text, words, raw = self._run_mlx(audio_path, language)
            version = f"mlx-whisper {_version('mlx-whisper')}"
        else:
            text, words, raw = self._run_faster_whisper(audio_path, language)
            version = f"faster-whisper {_version('faster-whisper')}"
        latency = (time.perf_counter() - t0) * 1000.0
        try:
            import soundfile as sf

            seconds = float(sf.info(str(audio_path)).duration)
        except Exception:
            seconds = 0.0
        return Result(
            text=text,
            words=words or None,
            latency_ms=latency,
            audio_seconds=seconds,
            cost_usd=0.0,
            raw_response=raw,
            model_id=self.model_id,
            provider_version=version,
            detected_language=normalize_language_code((raw or {}).get("language")) if self.detect_language else None,
        )

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        result = await asyncio.to_thread(self._transcribe_sync, Path(audio_path), language)
        if clip is not None and not result.audio_seconds:
            result.audio_seconds = clip.duration_s
        return result


class WhisperLocalTurbo(WhisperLocal):
    id: ClassVar[str] = "whisper-local-turbo"
    mlx_model: ClassVar[str] = "mlx-community/whisper-large-v3-turbo"
    fw_model: ClassVar[str] = "large-v3-turbo"
