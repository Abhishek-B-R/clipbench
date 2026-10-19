"""ElevenLabs Scribe v2 Realtime. https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime

Checked 2026-10-19:
- `wss://api.elevenlabs.io/v1/speech-to-text/realtime` with the `xi-api-key` header; query parameters
  `model_id=scribe_v2_realtime`, `audio_format=pcm_16000`, `language_code` (ISO 639-1 or 639-3),
  `commit_strategy` (`manual`, `vad`, `turn_prediction`), `include_timestamps`, `enable_logging`
  (false = zero retention, Enterprise only).
- The server sends `session_started` first; audio goes as JSON `input_audio_chunk` messages with base64 PCM
  (`audio_base_64`, `commit`, `sample_rate`). A chunk with `commit: true` and empty audio finalises the
  current segment.
- `partial_transcript` (`text`) is the interim for the current segment; `committed_transcript` and, with
  `include_timestamps`, `committed_transcript_with_timestamps` (`words[]` with `text`, `start`, `end` in
  seconds, `type` word or spacing) finalise it. Error messages have a `message_type` ending in `error`.
- No terminal message: the session ends after a quiet period following the final commit.
- 90+ languages, including en, es, hi, ja, ko. 0.39 USD per hour on pay as you go.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, ClassVar
from urllib.parse import urlencode

from clipbench.models import Word
from clipbench.streaming.base import Conn, Parsed, StreamError, StreamingProvider, recv_json

READY_TIMEOUT_S = 15.0


class ElevenLabsStream(StreamingProvider):
    id: ClassVar[str] = "elevenlabs-stream"
    env_keys: ClassVar[tuple[str, ...]] = ("ELEVENLABS_API_KEY",)
    concurrency: ClassVar[int] = 2
    quiet_s: ClassVar[float | None] = 2.0
    base_url: ClassVar[str] = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("commit_strategy", "vad")
        if os.environ.get("CLIPBENCH_ELEVENLABS_ZERO_RETENTION") == "1":
            kwargs.setdefault("enable_logging", False)
        super().__init__(model_id, **kwargs)

    def url(self, language: str) -> str:
        query = {
            "model_id": self.model_id,
            "audio_format": f"pcm_{self.sample_rate}",
            "language_code": language.split("-")[0].lower(),
            "commit_strategy": str(self.options["commit_strategy"]),
            "include_timestamps": "true",
        }
        if self.options.get("enable_logging") is False:
            query["enable_logging"] = "false"
        return f"{self.base_url}?{urlencode(query)}"

    def headers(self) -> dict[str, str]:
        return {"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")}

    async def start(self, conn: Conn, language: str, state: dict[str, Any]) -> None:
        while True:
            msg = await asyncio.wait_for(recv_json(conn), timeout=READY_TIMEOUT_S)
            self._raise_on_error(msg)
            if msg.get("message_type") == "session_started":
                if msg.get("session_id"):
                    state["meta"]["session_id"] = str(msg["session_id"])
                return

    def audio_message(self, chunk: bytes) -> str | bytes:
        return json.dumps(
            {
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(chunk).decode(),
                "commit": False,
                "sample_rate": self.sample_rate,
            }
        )

    def end_messages(self) -> list[str | bytes]:
        return [
            json.dumps(
                {"message_type": "input_audio_chunk", "audio_base_64": "", "commit": True,
                 "sample_rate": self.sample_rate}
            )
        ]

    def new_state(self) -> dict[str, Any]:
        return {"meta": {}, "segment": 0}

    def _raise_on_error(self, msg: dict[str, Any]) -> None:
        kind = str(msg.get("message_type") or "")
        if kind.endswith("error") or kind in ("quota_exceeded", "rate_limited", "auth_error"):
            raise StreamError(f"{self.id}: {kind}: {msg.get('error') or msg.get('message') or msg}")

    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        self._raise_on_error(message)
        kind = message.get("message_type")
        segment = str(state["segment"])
        if kind == "partial_transcript":
            return [Parsed("partial", segment, str(message.get("text") or "").strip(), None)]
        if kind == "committed_transcript_with_timestamps":
            state["segment"] += 1
            text = str(message.get("text") or "").strip()
            words = [
                Word(text=str(w.get("text", "")), start_ms=round(float(w["start"]) * 1000),
                     end_ms=round(float(w["end"]) * 1000))
                for w in message.get("words") or []
                if w.get("type", "word") == "word" and "start" in w and "end" in w
            ]
            if not text:
                return []
            return [Parsed("final", segment, text, words or None)]
        # With include_timestamps the plain committed_transcript repeats the timed one; it is ignored.
        return []
