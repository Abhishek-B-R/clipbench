"""Groq hosted Whisper. https://console.groq.com/docs/speech-to-text"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds


class GroqWhisper(HttpProvider):
    id: ClassVar[str] = "groq-whisper"
    env_keys: ClassVar[tuple[str, ...]] = ("GROQ_API_KEY",)
    concurrency: ClassVar[int] = 2  # 20 RPM / 7.2k audio seconds per hour on the listed tier
    timeout_s: ClassVar[float] = 120.0
    base_url: ClassVar[str] = "https://api.groq.com/openai/v1"

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("temperature", 0.0)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}"}

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        data = {
            "model": self.model_id,
            "language": language,  # ISO-639-1: ja, es, ko
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "temperature": str(self.options["temperature"]),
        }
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
        )
