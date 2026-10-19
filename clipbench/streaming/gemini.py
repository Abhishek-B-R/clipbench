"""Gemini Live API transcription (gemini-3.5-transcribe-live). https://ai.google.dev/gemini-api/docs/live-api/live-transcribe

Checked 2026-10-19:
- `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent`.
  The docs put the key in a `key` query parameter; this adapter sends it as the `x-goog-api-key` header
  instead (as the batch adapter does) so it never appears in a URL or a log. UNVERIFIED for the WebSocket.
- First message `setup` with `model`, `generationConfig.responseModalities = ["TEXT"]` and
  `inputAudioTranscription.languageCodes` (BCP-47, e.g. ja-JP); the server answers `setupComplete`.
- Audio as `realtimeInput.audio` (base64 16-bit PCM, `audio/pcm;rate=16000`), about 100 ms per chunk;
  `realtimeInput.audioStreamEnd` ends the stream.
- `serverContent.interimInputTranscription.text` is the speculative partial, `serverContent.inputTranscription.text`
  the final for the utterance. No word timestamps over the Live API.
- Sessions stream for up to 10 minutes. No terminal message: the session ends after a quiet period.
- 0.005 USD per minute audio input plus about 0.004 per minute text output (pricing page blended estimate
  0.009 USD per minute). Paid tier data is not used to improve products.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, ClassVar

from clipbench.streaming.base import Conn, Parsed, StreamError, StreamingProvider, recv_json

READY_TIMEOUT_S = 15.0

# Live transcription takes BCP-47 codes from its own table (a plain `es` is not listed).
LANGUAGE_CODES = {"en": "en-US", "ja": "ja-JP", "ko": "ko-KR", "es": "es-419", "hi": "hi-IN"}


class GeminiStream(StreamingProvider):
    id: ClassVar[str] = "gemini-stream"
    env_keys: ClassVar[tuple[str, ...]] = ("GEMINI_API_KEY",)
    concurrency: ClassVar[int] = 2
    quiet_s: ClassVar[float | None] = 3.0
    max_session_s: ClassVar[float | None] = 600.0
    base_url: ClassVar[str] = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )

    def url(self, language: str) -> str:
        return self.base_url

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")}

    def setup_message(self, language: str) -> dict[str, Any]:
        base = language.split("-")[0].lower()
        return {
            "setup": {
                "model": f"models/{self.model_id}",
                "generationConfig": {"responseModalities": ["TEXT"]},
                "inputAudioTranscription": {"languageCodes": [LANGUAGE_CODES.get(base, language)]},
            }
        }

    async def start(self, conn: Conn, language: str, state: dict[str, Any]) -> None:
        await conn.send(json.dumps(self.setup_message(language)))
        while True:
            msg = await asyncio.wait_for(recv_json(conn), timeout=READY_TIMEOUT_S)
            self._raise_on_error(msg)
            if "setupComplete" in msg:
                return

    def audio_message(self, chunk: bytes) -> str | bytes:
        return json.dumps(
            {"realtimeInput": {"audio": {"data": base64.b64encode(chunk).decode(),
                                         "mimeType": f"audio/pcm;rate={self.sample_rate}"}}}
        )

    def end_messages(self) -> list[str | bytes]:
        return [json.dumps({"realtimeInput": {"audioStreamEnd": True}})]

    def new_state(self) -> dict[str, Any]:
        return {"meta": {}, "segment": 0}

    def _raise_on_error(self, msg: dict[str, Any]) -> None:
        if "error" in msg:
            raise StreamError(f"{self.id}: {msg['error']}")

    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        self._raise_on_error(message)
        usage = message.get("usageMetadata")
        if isinstance(usage, dict):
            state["meta"]["usage"] = usage
        content = message.get("serverContent") or {}
        out: list[Parsed] = []
        interim = content.get("interimInputTranscription") or {}
        if interim.get("text"):
            out.append(Parsed("partial", str(state["segment"]), str(interim["text"]).strip(), None))
        final = content.get("inputTranscription") or {}
        if final.get("text") and str(final["text"]).strip():
            out.append(Parsed("final", str(state["segment"]), str(final["text"]).strip(), None))
            state["segment"] += 1
        return out
