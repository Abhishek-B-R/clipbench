"""ElevenLabs Scribe. https://elevenlabs.io/docs/api-reference/speech-to-text/convert"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds

TRUTHY = {"1", "true", "yes", "on"}


class ElevenLabsScribe(HttpProvider):
    id: ClassVar[str] = "elevenlabs-scribe"
    env_keys: ClassVar[tuple[str, ...]] = ("ELEVENLABS_API_KEY",)
    concurrency: ClassVar[int] = 4
    timeout_s: ClassVar[float] = 180.0
    base_url: ClassVar[str] = "https://api.elevenlabs.io/v1"

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("tag_audio_events", False)
        kwargs.setdefault("diarize", False)
        super().__init__(model_id, **kwargs)
        # Zero retention mode is Enterprise only; other plans reject enable_logging=false.
        env = os.environ.get("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", "").strip().lower()
        self.zero_retention = bool(kwargs.get("zero_retention", env in TRUTHY))

    def settings(self) -> dict[str, Any]:
        s = super().settings()
        s.pop("zero_retention", None)  # does not change the output
        return s

    def headers(self) -> dict[str, str]:
        return {"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")}

    def form(self, language: str) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "language_code": language,  # ISO-639-1: ja, es, ko
            "timestamps_granularity": "word",
            "tag_audio_events": str(bool(self.options["tag_audio_events"])).lower(),
            "diarize": str(bool(self.options["diarize"])).lower(),
        }

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        params = {"enable_logging": "false"} if self.zero_retention else None
        async with self.slot(), stopwatch() as watch:
            resp = await self.http.request(
                "POST",
                "/speech-to-text",
                params=params,
                data=self.form(language),
                files={"file": (audio_path.name, audio, "audio/wav")},
            )
            body = resp.json()
        words = [
            Word(
                text=w["text"], start_ms=round(float(w["start"]) * 1000), end_ms=round(float(w["end"]) * 1000)
            )
            for w in body.get("words") or []
            if w.get("type", "word") == "word" and w.get("start") is not None
        ]
        return Result(
            text=(body.get("text") or "").strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(seconds),
            raw_response=body,
            model_id=self.model_id,
            provider_version=None,  # the API reports no model version
        )
