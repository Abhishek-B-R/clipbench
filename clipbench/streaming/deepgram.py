"""Deepgram live streaming (Nova-3). https://developers.deepgram.com/reference/speech-to-text/listen-streaming

Checked 2026-10-19:
- `wss://api.deepgram.com/v1/listen` with `Authorization: Token <key>`; options as query parameters
  (model, language, encoding=linear16, sample_rate, channels, interim_results, punctuate, smart_format,
  mip_opt_out). Audio goes as binary frames.
- Server `Results` messages: `is_final` false is an interim (partial) for the current segment, `is_final` true
  finalises it; `channel.alternatives[0].transcript` and `.words[]` (`word`, `punctuated_word`, `start`, `end`
  in seconds from the stream start). `{"type": "CloseStream"}` flushes the remaining audio, the server sends
  the last finals and a `Metadata` message, then closes. `Error` messages carry `description`.
- Nova-3 lists en, es, hi, ja and ko.
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar
from urllib.parse import urlencode

from clipbench.models import Word
from clipbench.streaming.base import Parsed, StreamError, StreamingProvider


def deepgram_words(alt: dict[str, Any]) -> list[Word] | None:
    words = [
        Word(
            text=str(w.get("punctuated_word") or w.get("word") or ""),
            start_ms=round(float(w["start"]) * 1000),
            end_ms=round(float(w["end"]) * 1000),
        )
        for w in alt.get("words") or []
        if "start" in w and "end" in w
    ]
    return words or None


class DeepgramShaped(StreamingProvider):
    """Deepgram's live message format. Also served by Cloudflare's hosted Nova-3."""

    def query(self, language: str) -> dict[str, str]:
        return {
            "model": self.model_id,
            "language": language,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            "smart_format": "true",
            "mip_opt_out": "true",  # out of the Model Improvement Program
        }

    def end_messages(self) -> list[str | bytes]:
        return [json.dumps({"type": "CloseStream"})]

    def new_state(self) -> dict[str, Any]:
        return {"meta": {}, "segment": 0}

    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        kind = message.get("type")
        if kind == "Error":
            raise StreamError(f"{self.id}: {message.get('description') or message.get('message') or message}")
        if kind == "Metadata":
            if message.get("duration") is not None:
                state["meta"]["billed_audio_s"] = float(message["duration"])
            if message.get("request_id"):
                state["meta"]["request_id"] = str(message["request_id"])
            return []
        if kind != "Results":
            return []
        info = (message.get("metadata") or {}).get("model_info") or {}
        if info and "model_version" not in state["meta"]:
            state["meta"]["model_version"] = f"{info.get('name', '')}:{info.get('version', '')}".strip(":")
        alts = (message.get("channel") or {}).get("alternatives") or [{}]
        alt = alts[0]
        text = str(alt.get("transcript") or "").strip()
        segment = str(state["segment"])
        if message.get("is_final"):
            state["segment"] += 1
            if not text:
                return []
            return [Parsed("final", segment, text, deepgram_words(alt))]
        return [Parsed("partial", segment, text, None)]

    def is_terminal(self, message: dict[str, Any], state: dict[str, Any]) -> bool:
        return message.get("type") == "Metadata"


class DeepgramStream(DeepgramShaped):
    id: ClassVar[str] = "deepgram-stream"
    env_keys: ClassVar[tuple[str, ...]] = ("DEEPGRAM_API_KEY",)
    concurrency: ClassVar[int] = 4
    base_url: ClassVar[str] = "wss://api.deepgram.com/v1/listen"

    def url(self, language: str) -> str:
        return f"{self.base_url}?{urlencode(self.query(language))}"

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Token {os.environ.get('DEEPGRAM_API_KEY', '')}"}
