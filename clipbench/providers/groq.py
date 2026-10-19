"""Groq hosted Whisper. https://console.groq.com/docs/speech-to-text

Run options (checked 2026-10-19):
- diarize: not offered.
- detect_language: no `language` field (Whisper detects it); `verbose_json` carries `language`
  (OpenAI compatible: a lowercase English name such as "japanese", normalised to "ja"). The docs do
  not show that field, so this needs a live check.
- keyterms: Whisper `prompt` (224 tokens), the terms joined with ", ". We cap the prompt at
  PROMPT_MAX_CHARS characters as a conservative stand in for the token limit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import normalize_language_code
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds

PROMPT_MAX_CHARS = 400


def keyterm_prompt(terms: list[str], max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Whole terms joined with ', ' until the next one would pass max_chars."""
    out: list[str] = []
    size = 0
    for t in terms:
        extra = len(t) + (2 if out else 0)
        if size + extra > max_chars:
            break
        out.append(t)
        size += extra
    return ", ".join(out)


class GroqWhisper(HttpProvider):
    id: ClassVar[str] = "groq-whisper"
    env_keys: ClassVar[tuple[str, ...]] = ("GROQ_API_KEY",)
    concurrency: ClassVar[int] = 2  # 20 RPM / 7.2k audio seconds per hour on the listed tier
    timeout_s: ClassVar[float] = 120.0
    base_url: ClassVar[str] = "https://api.groq.com/openai/v1"
    supported_options: ClassVar[frozenset[str]] = frozenset({"detect_language", "keyterms"})

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("temperature", 0.0)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}"}

    def form(self, language: str) -> dict[str, str]:
        data = {"model": self.model_id}
        if not self.detect_language:
            data["language"] = language  # ISO-639-1: ja, es, ko
        data.update(
            {
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
                "temperature": str(self.options["temperature"]),
            }
        )
        prompt = keyterm_prompt(self.keyterms)
        if prompt:
            data["prompt"] = prompt
        return data

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        data = self.form(language)
        async with self.slot(), stopwatch() as watch:
            resp = await self.http.request(
                "POST",
                "/audio/transcriptions",
                data=data,
                files={"file": (audio_path.name, audio, "audio/wav")},
            )
            body = resp.json()
        words = [
            Word(
                text=w["word"], start_ms=round(float(w["start"]) * 1000), end_ms=round(float(w["end"]) * 1000)
            )
            for w in body.get("words") or []
        ]
        return Result(
            text=(body.get("text") or "").strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(seconds),  # Groq reports no cost; 10 s minimum per request
            raw_response=body,
            model_id=self.model_id,
            provider_version=None,  # Groq exposes no model version beyond the model id
            detected_language=normalize_language_code(body.get("language")) if self.detect_language else None,
        )
