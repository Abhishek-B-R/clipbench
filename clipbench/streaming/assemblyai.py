"""AssemblyAI Universal-Streaming v3. https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket

Checked 2026-10-19:
- `wss://streaming.assemblyai.com/v3/ws` with the API key in the `Authorization` header (no Bearer prefix);
  options as query parameters: `speech_model` (default `universal-3-6-pro`), `encoding=pcm_s16le`,
  `sample_rate`, `language_codes` (steering, 3.6 Pro and 3.5 Pro only). Unknown parameters are ignored, not
  rejected, so the `Begin` message's `configuration` echo is kept in the transcript meta for checking.
- Audio as binary frames of 50 to 1000 ms, paced near real time.
- `Turn` messages: `turn_order`, `end_of_turn`, `transcript` (final words so far), `words[]` with `text`,
  `start`, `end` (ms) and `word_is_final`. A turn with `end_of_turn` true is the final for that turn.
  `{"type": "Terminate"}` ends the session; the server answers with `Termination`
  (`audio_duration_seconds`, `session_duration_seconds`).
- Universal-3.6 Pro covers en, es, hi, ja and ko (3.5 Pro has no ko).
- Billed by session duration (the socket's open time), 0.45 USD per hour for Universal-3.6 Pro.

UNVERIFIED: how a list goes into `language_codes` on the query string; a single code is sent as
`language_codes=<code>`.
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar
from urllib.parse import urlencode

from clipbench.models import Word
from clipbench.streaming.base import Parsed, StreamError, StreamingProvider


class AssemblyAIStream(StreamingProvider):
    id: ClassVar[str] = "assemblyai-stream"
    env_keys: ClassVar[tuple[str, ...]] = ("ASSEMBLYAI_API_KEY",)
    concurrency: ClassVar[int] = 4
    billing: ClassVar[str] = "session"
    base_url: ClassVar[str] = "wss://streaming.assemblyai.com/v3/ws"

    def url(self, language: str) -> str:
        query = {
            "speech_model": self.model_id,
            "encoding": "pcm_s16le",
            "sample_rate": str(self.sample_rate),
            "language_codes": language.split("-")[0].lower(),
        }
        return f"{self.base_url}?{urlencode(query)}"

    def headers(self) -> dict[str, str]:
        return {"authorization": os.environ.get("ASSEMBLYAI_API_KEY", "")}

    def end_messages(self) -> list[str | bytes]:
        return [json.dumps({"type": "Terminate"})]

    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        kind = message.get("type")
        if kind == "Begin":
            state["meta"]["session_id"] = message.get("id")
            config = message.get("configuration")
            if isinstance(config, dict):
                state["meta"]["configuration"] = config
                if config.get("model"):
                    state["meta"]["model_version"] = str(config["model"])
            return []
        if kind == "Termination":
            if message.get("session_duration_seconds") is not None:
                state["meta"]["billed_session_s"] = float(message["session_duration_seconds"])
            if message.get("audio_duration_seconds") is not None:
                state["meta"]["billed_audio_s"] = float(message["audio_duration_seconds"])
            return []
        if kind == "Error" or "error" in message:
            raise StreamError(f"{self.id}: {message.get('error') or message}")
        if kind != "Turn":
            return []
        segment = str(message.get("turn_order", 0))
        raw_words = message.get("words") or []
        if message.get("end_of_turn"):
            text = str(message.get("transcript") or message.get("utterance") or "").strip()
            words = [
                Word(text=str(w.get("text", "")), start_ms=int(round(float(w["start"]))),
                     end_ms=int(round(float(w["end"]))))
                for w in raw_words
                if "start" in w and "end" in w
            ]
            if not text:
                return []
            return [Parsed("final", segment, text, words or None)]
        # Interim: every word heard so far in the turn, final or not.
        text = " ".join(str(w.get("text", "")) for w in raw_words).strip() or str(message.get("transcript") or "")
        return [Parsed("partial", segment, text.strip(), None)]

    def is_terminal(self, message: dict[str, Any], state: dict[str, Any]) -> bool:
        return message.get("type") == "Termination"
