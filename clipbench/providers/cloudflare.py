"""Cloudflare Workers AI speech recognition. https://developers.cloudflare.com/workers-ai/models/

One REST endpoint for every model:
    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{model}
with `Authorization: Bearer <API token with Workers AI permission>`. The response is wrapped in the
usual Cloudflare envelope `{"result": {...}, "success": true, "errors": [], "messages": []}`.

Request shapes differ per model (from each model's schema-input.json, checked 2026-10-19):
- `@cf/openai/whisper-large-v3-turbo`: JSON `{"audio": <base64>, "language", "task"}`. Words are
  nested in `segments[].words`.
- `@cf/openai/whisper` and `@cf/openai/whisper-tiny-en`: raw audio bytes as the body, no language
  parameter (language is auto detected). Words are top level `words[]`.
- `@cf/deepgram/nova-3`: raw audio bytes with a content-type, options as query parameters, Deepgram
  shaped output `results.channels[0].alternatives[0].words`.

Run options (checked 2026-10-19 on each model's schema):
- whisper-large-v3-turbo: detect_language (omit `language`, read `transcription_info.language`) and
  keyterms (`initial_prompt` with the terms). No diarization.
- whisper and whisper-tiny-en: no options. They never take a language (always auto detect) but report
  no detected language, so detect_language is recorded as unsupported.
- deepgram/nova-3: `diarize`, `detect_language` and `keyterm` as Deepgram query parameters. The output
  schema does not list `speaker` or `detected_language`; they are read from the Deepgram shape when
  present (needs a live check).

Billing is in neurons (USD 0.011 per 1,000, first 10,000 per day free). No model documents a request
size or duration limit, so long files are split into WAV chunks of `chunk_seconds` (Whisper models)
and any single request body above `max_request_bytes` fails with a clear ProviderError.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any, ClassVar

import soundfile

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import ProviderError, ProviderSkip, load_pricing, normalize_language_code
from clipbench.providers.deepgram import MAX_KEYTERMS, parse_alternative
from clipbench.providers.groq import keyterm_prompt
from clipbench.providers.http import SHAPE_ERRORS, HttpProvider, shape_error, stopwatch, wav_seconds

ALL_LANGUAGES = frozenset({"en", "ja", "ko", "es", "hi"})


class WorkersAIProvider(HttpProvider):
    """Shared plumbing. Subclasses set `id`, `languages` and implement `build_request` / `parse`."""

    env_keys: ClassVar[tuple[str, ...]] = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    # Automatic Speech Recognition is limited to 720 requests per minute per account.
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 180.0
    base_url: ClassVar[str] = "https://api.cloudflare.com/client/v4"
    languages: ClassVar[frozenset[str]] = ALL_LANGUAGES
    # Split audio longer than this into separate requests. None means never split.
    default_chunk_seconds: ClassVar[float | None] = 240.0
    # No documented limit; this keeps a runaway file from being uploaded at all.
    max_request_bytes: ClassVar[int] = 25 * 1024 * 1024

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        if self.default_chunk_seconds is not None:
            kwargs.setdefault("chunk_seconds", self.default_chunk_seconds)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {os.environ.get('CLOUDFLARE_API_TOKEN', '')}"}

    def run_path(self) -> str:
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        return f"/accounts/{account}/ai/run/{self.model_id}"

    # ---------------------------------------------------------------- pricing

    def _pricing(self) -> dict[str, Any]:
        return load_pricing().get(self.id, {})

    def neuron_cost(self, neurons: float) -> float:
        return float(neurons) * float(self._pricing().get("usd_per_1k_neurons", 0.0)) / 1000.0

    @staticmethod
    def reported_neurons(body: dict[str, Any]) -> float | None:
        """Neurons billed, if the response reports them. The schemas document none today."""
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        for usage in (body.get("usage"), result.get("usage")):
            if isinstance(usage, dict) and isinstance(usage.get("neurons"), int | float):
                return float(usage["neurons"])
        return None

    # ---------------------------------------------------------------- per model hooks

    def build_request(self, audio: bytes, language: str) -> dict[str, Any]:
        raise NotImplementedError

    def parse(self, result: dict[str, Any]) -> tuple[str, list[Word]]:
        raise NotImplementedError

    def version(self, result: dict[str, Any]) -> str | None:
        return None

    def extras(self, result: dict[str, Any]) -> tuple[list[str | None] | None, str | None]:
        """(speakers parallel to parse()'s words, detected language). Default: neither."""
        return None, None

    # ---------------------------------------------------------------- audio

    def _chunks(self, audio_path: Path, seconds: float) -> list[tuple[float, bytes]]:
        """[(offset_seconds, wav_bytes)]. The original file is sent untouched when it fits."""
        chunk_s = self.options.get("chunk_seconds")
        if not chunk_s or seconds <= float(chunk_s):
            return [(0.0, audio_path.read_bytes())]
        data, sr = soundfile.read(str(audio_path), dtype="int16", always_2d=False)
        step = int(float(chunk_s) * sr)
        out: list[tuple[float, bytes]] = []
        for start in range(0, len(data), step):
            buf = io.BytesIO()
            soundfile.write(buf, data[start : start + step], sr, format="WAV", subtype="PCM_16")
            out.append((start / sr, buf.getvalue()))
        return out

    # ---------------------------------------------------------------- main

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if language not in self.languages:
            raise ProviderSkip(f"{self.id}: {self.model_id} does not support language {language!r}")
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        chunks = self._chunks(audio_path, seconds)
        for _, blob in chunks:
            if len(blob) > self.max_request_bytes:
                raise ProviderError(
                    f"{self.id}: request of {len(blob)} bytes exceeds {self.max_request_bytes}; "
                    "lower chunk_seconds or downsample the audio"
                )
        texts: list[str] = []
        words: list[Word] = []
        bodies: list[dict[str, Any]] = []
        neurons: float | None = 0.0
        speakers: list[str | None] = []
        any_speakers = False
        detected: str | None = None
        async with self.slot(), stopwatch() as watch:
            for offset, blob in chunks:
                resp = await self.http.request("POST", self.run_path(), **self.build_request(blob, language))
                try:
                    body = resp.json()
                except ValueError as e:
                    raise ProviderError(f"{self.id}: non-JSON response: {resp.text[:300]}") from e
                if not isinstance(body, dict):
                    raise shape_error(self.id, body)
                if body.get("success") is False or not isinstance(body.get("result"), dict):
                    raise ProviderError(f"{self.id}: request failed: {body.get('errors') or body}")
                bodies.append(body)
                try:
                    text, chunk_words = self.parse(body["result"])
                    chunk_speakers, chunk_lang = self.extras(body["result"])
                except SHAPE_ERRORS as e:
                    raise shape_error(self.id, body, e) from e
                texts.append(text)
                if chunk_speakers is not None and len(chunk_speakers) == len(chunk_words):
                    speakers.extend(chunk_speakers)
                    any_speakers = True
                else:
                    speakers.extend([None] * len(chunk_words))
                detected = detected or chunk_lang
                shift = round(offset * 1000)
                words.extend(
                    Word(text=w.text, start_ms=w.start_ms + shift, end_ms=w.end_ms + shift)
                    for w in chunk_words
                )
                used = self.reported_neurons(body)
                neurons = None if neurons is None or used is None else neurons + used
        joiner = "" if language == "ja" else " "
        cost = self.neuron_cost(neurons) if neurons else self.estimate_cost(seconds)
        return Result(
            text=joiner.join(t for t in texts if t).strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=cost,
            raw_response=bodies[0] if len(bodies) == 1 else bodies,
            model_id=self.model_id,
            provider_version=self.version(bodies[0]["result"]),
            speakers=speakers if any_speakers and words else None,
            detected_language=detected if self.detect_language else None,
        )


def _word(w: dict[str, Any], key: str = "word") -> Word:
    return Word(
        text=str(w.get(key) or w.get("word") or "").strip(),
        start_ms=round(float(w["start"]) * 1000),
        end_ms=round(float(w["end"]) * 1000),
    )


class CfWhisperLargeV3Turbo(WorkersAIProvider):
    """@cf/openai/whisper-large-v3-turbo: base64 JSON, takes a language, words inside segments."""

    id: ClassVar[str] = "cf-whisper-large-v3-turbo"
    supported_options: ClassVar[frozenset[str]] = frozenset({"detect_language", "keyterms"})

    def build_request(self, audio: bytes, language: str) -> dict[str, Any]:
        body: dict[str, Any] = {"audio": base64.b64encode(audio).decode("ascii"), "task": "transcribe"}
        if not self.detect_language:
            body["language"] = language
        prompt = keyterm_prompt(self.keyterms)
        if prompt:
            body["initial_prompt"] = prompt
        return {"json": body}

    def extras(self, result: dict[str, Any]) -> tuple[list[str | None] | None, str | None]:
        info = result.get("transcription_info") or {}
        return None, normalize_language_code(info.get("language"))

    def parse(self, result: dict[str, Any]) -> tuple[str, list[Word]]:
        words = [
            _word(w)
            for seg in result.get("segments") or []
            for w in seg.get("words") or []
            if "start" in w and "end" in w
        ]
        return (result.get("text") or "").strip(), [w for w in words if w.text]


class CfWhisper(WorkersAIProvider):
    """@cf/openai/whisper: raw bytes, no language parameter (auto detect), top level words."""

    id: ClassVar[str] = "cf-whisper"

    def build_request(self, audio: bytes, language: str) -> dict[str, Any]:
        # The schema has no language field; the model detects it. Kept in mind when comparing.
        return {"content": audio, "headers": {"content-type": "application/octet-stream"}}

    def parse(self, result: dict[str, Any]) -> tuple[str, list[Word]]:
        words = [_word(w) for w in result.get("words") or [] if "start" in w and "end" in w]
        return (result.get("text") or "").strip(), [w for w in words if w.text]


class CfWhisperTinyEn(CfWhisper):
    """@cf/openai/whisper-tiny-en (beta): English only, same shape as @cf/openai/whisper."""

    id: ClassVar[str] = "cf-whisper-tiny-en"
    languages: ClassVar[frozenset[str]] = frozenset({"en"})


class CfDeepgramNova3(WorkersAIProvider):
    """@cf/deepgram/nova-3 over HTTP: raw bytes, Deepgram options as query parameters."""

    id: ClassVar[str] = "cf-deepgram-nova-3"
    default_chunk_seconds: ClassVar[float | None] = None  # Deepgram handles long files itself
    supported_options: ClassVar[frozenset[str]] = frozenset({"diarize", "detect_language", "keyterms"})

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("smart_format", True)
        kwargs.setdefault("punctuate", True)
        super().__init__(model_id, **kwargs)

    def build_request(self, audio: bytes, language: str) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.detect_language:
            params["detect_language"] = "true"
        else:
            params["language"] = language  # BCP-47; en, ja, ko, es, hi are all Nova-3 batch languages
        params.update(
            {
                "smart_format": str(bool(self.options["smart_format"])).lower(),
                "punctuate": str(bool(self.options["punctuate"])).lower(),
                "mip_opt_out": "true",  # out of Deepgram's Model Improvement Program
            }
        )
        if self.diarize:
            params["diarize"] = "true"  # the Workers AI schema takes a boolean, not diarize_model
        if self.keyterms:
            params["keyterm"] = self.keyterms[:MAX_KEYTERMS]
        return {"params": params, "content": audio, "headers": {"content-type": "audio/wav"}}

    def parse(self, result: dict[str, Any]) -> tuple[str, list[Word]]:
        try:
            alt = result["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.id}: unexpected response shape: {str(result)[:300]}") from e
        words = [_word(w, "punctuated_word") for w in alt.get("words") or []]
        return (alt.get("transcript") or "").strip(), [w for w in words if w.text]

    def extras(self, result: dict[str, Any]) -> tuple[list[str | None] | None, str | None]:
        try:
            _, words, speakers, detected = parse_alternative(result)
        except (KeyError, IndexError, TypeError):
            return None, None
        if speakers is not None:
            speakers = [sp for w, sp in zip(words, speakers, strict=True) if w.text.strip()]
        return speakers, detected

    def version(self, result: dict[str, Any]) -> str | None:
        infos = list(((result.get("metadata") or {}).get("model_info") or {}).values())
        if not infos:
            return None
        return f"{infos[0].get('name', '')}:{infos[0].get('version', '')}".strip(":") or None
