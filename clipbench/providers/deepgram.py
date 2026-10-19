"""Deepgram pre-recorded. https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds


class Deepgram(HttpProvider):
    id: ClassVar[str] = "deepgram"
    env_keys: ClassVar[tuple[str, ...]] = ("DEEPGRAM_API_KEY",)
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 120.0
    base_url: ClassVar[str] = "https://api.deepgram.com/v1"

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("smart_format", True)
        kwargs.setdefault("punctuate", True)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Token {os.environ.get('DEEPGRAM_API_KEY', '')}"}

    def params(self, language: str) -> dict[str, str]:
        return {
            "model": self.model_id,
            "language": language,  # ja, es, ko
            "smart_format": str(bool(self.options["smart_format"])).lower(),
            "punctuate": str(bool(self.options["punctuate"])).lower(),
            "mip_opt_out": "true",  # out of the Model Improvement Program
        }

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        async with self.slot(), stopwatch() as watch:
            resp = await self.http.request(
                "POST",
                "/listen",
                params=self.params(language),
                content=audio,
                headers={"content-type": "audio/wav"},
            )
            body = resp.json()
        alt = body["results"]["channels"][0]["alternatives"][0]
        words = [
            Word(
                text=w.get("punctuated_word") or w["word"],
                start_ms=round(float(w["start"]) * 1000),
                end_ms=round(float(w["end"]) * 1000),
            )
            for w in alt.get("words") or []
        ]
        meta = body.get("metadata") or {}
        billed = float(meta.get("duration") or seconds)
        infos = list((meta.get("model_info") or {}).values())
        version = None
        if infos:
            version = f"{infos[0].get('name', '')}:{infos[0].get('version', '')}".strip(":")
        return Result(
            text=(alt.get("transcript") or "").strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(billed),
            raw_response=body,
            model_id=self.model_id,
            provider_version=version,
        )
