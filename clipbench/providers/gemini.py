"""Gemini Flash as a transcriber. https://ai.google.dev/gemini-api/docs/audio

Audio is sent inline when it fits under the 20 MB request limit, otherwise through the Files API
(uploaded, used, then deleted). Cost is computed from usageMetadata in the response.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result
from clipbench.providers.base import ProviderError, load_pricing
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds

LANGUAGE_NAMES = {"ja": "Japanese", "es": "Spanish", "ko": "Korean", "en": "English"}

PROMPT = (
    "Transcribe this audio verbatim in the language it is spoken ({language}). "
    "Do not translate. Do not summarize, correct, or add commentary. "
    "No timestamps, no speaker labels, no sound descriptions, no markdown. "
    "Output only the transcript text. If there is no speech, output nothing."
)

# base64 inflates by 4/3; keep the whole request comfortably under 20 MB.
INLINE_MAX_BYTES = 14 * 1024 * 1024


class GeminiFlash(HttpProvider):
    id: ClassVar[str] = "gemini-flash"
    env_keys: ClassVar[tuple[str, ...]] = ("GEMINI_API_KEY",)
    concurrency: ClassVar[int] = 4
    timeout_s: ClassVar[float] = 180.0
    base_url: ClassVar[str] = "https://generativelanguage.googleapis.com"

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("temperature", 0.0)
        kwargs.setdefault("thinking_level", "low")  # 3.8 Flash cannot turn thinking off
        kwargs.setdefault("prompt_version", 1)
        super().__init__(model_id, **kwargs)
        self.inline_max_bytes = INLINE_MAX_BYTES

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")}

    def prompt(self, language: str) -> str:
        return PROMPT.format(language=LANGUAGE_NAMES.get(language, language))

    def request_body(self, audio_part: dict[str, Any], language: str) -> dict[str, Any]:
        gen: dict[str, Any] = {"temperature": self.options["temperature"]}
        if self.options.get("thinking_level"):
            gen["thinkingConfig"] = {"thinkingLevel": self.options["thinking_level"]}
        return {
            "contents": [{"role": "user", "parts": [{"text": self.prompt(language)}, audio_part]}],
            "generationConfig": gen,
        }

    async def _upload(self, audio: bytes, name: str) -> dict[str, Any]:
        start = await self.http.request(
            "POST",
            "/upload/v1beta/files",
            headers={
                "x-goog-upload-protocol": "resumable",
                "x-goog-upload-command": "start",
                "x-goog-upload-header-content-length": str(len(audio)),
                "x-goog-upload-header-content-type": "audio/wav",
            },
            json={"file": {"display_name": name}},
        )
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise ProviderError("gemini-flash: Files API did not return an upload URL")
        done = await self.http.json(
            "POST",
            upload_url,
            headers={"x-goog-upload-offset": "0", "x-goog-upload-command": "upload, finalize"},
            content=audio,
        )
        return done["file"]

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        uploaded: dict[str, Any] | None = None
        async with self.slot(), stopwatch() as watch:
            try:
                if len(audio) <= self.inline_max_bytes:
                    part = {
                        "inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(audio).decode()}
                    }
                else:
                    uploaded = await self._upload(audio, audio_path.name)
                    part = {"file_data": {"mime_type": "audio/wav", "file_uri": uploaded["uri"]}}
                body = await self.http.json(
                    "POST",
                    f"/v1beta/models/{self.model_id}:generateContent",
                    json=self.request_body(part, language),
                )
                latency = watch.ms
            finally:
                if uploaded and uploaded.get("name"):
                    try:
                        await self.http.request("DELETE", f"/v1beta/{uploaded['name']}")
                    except ProviderError:
                        pass
        block = (body.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise ProviderError(f"gemini-flash: prompt blocked: {block}")
        return Result(
            text=extract_text(body),
            words=None,
            latency_ms=latency,
            audio_seconds=seconds,
            cost_usd=self.cost_from_usage(body.get("usageMetadata"), seconds),
            raw_response=body,
            model_id=self.model_id,
            provider_version=body.get("modelVersion"),
        )

    def token_prices(self) -> dict[str, float] | None:
        table = load_pricing().get(self.id, {}).get("token_prices", {})
        return table.get(self.model_id)

    def cost_from_usage(self, usage: dict[str, Any] | None, seconds: float) -> float:
        prices = self.token_prices()
        if not usage or not prices:
            return self.estimate_cost(seconds)
        prompt_total = int(usage.get("promptTokenCount") or 0)
        audio = sum(
            int(d.get("tokenCount") or 0)
            for d in usage.get("promptTokensDetails") or []
            if str(d.get("modality", "")).upper() == "AUDIO"
        )
        other_in = max(prompt_total - audio, 0)
        out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
        usd = (
            audio * prices.get("audio_input", prices["input"])
            + other_in * prices["input"]
            + out * prices["output"]
        )
        return usd / 1_000_000


def extract_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):  # a fenced block despite the prompt
        lines = text.strip("`").splitlines()
        if lines and lines[0].strip().isalpha() and len(lines) > 1:
            lines = lines[1:]  # drop a language tag such as ```text
        text = "\n".join(lines).strip()
    return text
