"""A fake streaming provider and its in-memory server, for tests and offline pipeline checks. Free, no network.

Wire protocol: binary PCM frames in, `{"type": "end"}` to finish. The server sends
`{"type": "partial" | "final", "seg": n, "text": ..., "words": [...]}` and `{"type": "done"}` last.
`script_handler(text)` returns a server that grows a partial word by word while audio arrives and finalises
the whole text in one segment after the end message.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from clipbench.models import Word
from clipbench.streaming.base import Parsed, StreamError, StreamingProvider


class FakeStream(StreamingProvider):
    id: ClassVar[str] = "fake-stream"
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False
    concurrency: ClassVar[int] = 4
    verification_exempt: ClassVar[bool] = True  # test double, outside the verification gate

    @classmethod
    def default_model_id(cls) -> str:
        return "fake-stream-1"

    def price_per_minute(self) -> float:
        return float(self.options.get("usd_per_minute", 0.0))

    def url(self, language: str) -> str:
        return f"memory://fake-stream?language={language}"

    def end_messages(self) -> list[str | bytes]:
        return [json.dumps({"type": "end"})]

    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        kind = message.get("type")
        if kind == "error":
            raise StreamError(f"{self.id}: {message.get('message')}")
        if kind not in ("partial", "final"):
            return []
        words = None
        if message.get("words"):
            words = [Word(**w) for w in message["words"]]
        return [Parsed(kind, str(message.get("seg", 0)), str(message.get("text", "")), words)]

    def is_terminal(self, message: dict[str, Any], state: dict[str, Any]) -> bool:
        return message.get("type") == "done"


def script_handler(text: str, *, word_ms: int = 300):
    """Server for FakeStream: one partial per received chunk (one more word each time), one final at the end."""
    tokens = text.split()

    async def handler(server, url: str, headers: dict[str, str]) -> None:
        shown = 0
        while True:
            msg = await server.recv()
            if isinstance(msg, bytes):
                if shown < len(tokens):
                    shown += 1
                    await server.send(json.dumps({"type": "partial", "seg": 0, "text": " ".join(tokens[:shown])}))
                continue
            if json.loads(msg).get("type") == "end":
                words = [
                    {"text": t, "start_ms": i * word_ms, "end_ms": i * word_ms + word_ms - 50}
                    for i, t in enumerate(tokens)
                ]
                await server.send(json.dumps({"type": "final", "seg": 0, "text": text, "words": words}))
                await server.send(json.dumps({"type": "done"}))
                return

    return handler
