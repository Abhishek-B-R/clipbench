"""The streaming interface: a WebSocket provider, the connection it talks over, and the clock it is timed by.

A `StreamingProvider` only describes the wire protocol: where to connect, how to start a session, how to wrap
an audio chunk, how to end the stream and how to turn each server message into partial and final events. The
session loop (`clipbench.streaming.session`) owns timing, so every adapter is measured the same way.

The connection is anything with `send`, `recv` and `close` (`Conn`). Real runs use `websockets_connect`;
tests use the in-memory server in `clipbench.streaming.fakeserver`, so no test ever opens a socket.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, ClassVar, Literal, NamedTuple, Protocol

from pydantic import BaseModel, Field

from clipbench.models import Word
from clipbench.providers.base import ProviderSkip, load_pricing

ALL_LANGUAGES = frozenset({"en", "ja", "ko", "es", "hi"})

EventKind = Literal["partial", "final"]


class StreamClosed(Exception):
    """The other side closed the connection."""

    def __init__(self, code: int | None = None, reason: str = "") -> None:
        super().__init__(f"connection closed (code {code}){': ' + reason if reason else ''}")
        self.code = code
        self.reason = reason


class StreamError(Exception):
    """The provider reported an error, or the session could not finish."""


# ---------------------------------------------------------------- connection


class Conn(Protocol):
    async def send(self, data: str | bytes) -> None: ...

    async def recv(self) -> str | bytes:
        """Next message. Raises StreamClosed when the connection is closed."""
        ...

    async def close(self) -> None: ...


Connect = Callable[[str, dict[str, str]], AbstractAsyncContextManager[Conn]]


class _WebsocketsConn:
    def __init__(self, ws: Any) -> None:
        self.ws = ws

    async def send(self, data: str | bytes) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self.ws.send(data)
        except ConnectionClosed as exc:
            rcvd = exc.rcvd
            raise StreamClosed(rcvd.code if rcvd else None, rcvd.reason if rcvd else "") from exc

    async def recv(self) -> str | bytes:
        from websockets.exceptions import ConnectionClosed

        try:
            return await self.ws.recv()
        except ConnectionClosed as exc:
            rcvd = exc.rcvd
            raise StreamClosed(rcvd.code if rcvd else None, rcvd.reason if rcvd else "") from exc

    async def close(self) -> None:
        await self.ws.close()


@asynccontextmanager
async def websockets_connect(url: str, headers: dict[str, str]) -> AsyncIterator[Conn]:
    """Open a real WebSocket with the `websockets` package. Only used by live runs."""
    from websockets.asyncio.client import connect

    async with connect(
        url,
        additional_headers=headers,
        user_agent_header="clipbench/0.1",
        open_timeout=20,
        max_size=2**24,
        ping_interval=20,
    ) as ws:
        yield _WebsocketsConn(ws)


# ---------------------------------------------------------------- clock


class Clock(Protocol):
    def now(self) -> float:
        """Seconds, monotonic."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.perf_counter()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(seconds, 0.0))


class FakeClock:
    """For tests: time only moves when someone sleeps or sets it. sleep() still yields to the loop."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = max(self.t, float(t))

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.0)
        await asyncio.sleep(0)


# ---------------------------------------------------------------- transcript


class StreamEvent(BaseModel):
    t_ms: float  # wall time since the first audio byte was sent
    kind: EventKind
    segment: str  # provider's segment / turn key; a later final for the same segment replaces the earlier one
    text: str
    words: list[Word] | None = None


class StreamTranscript(BaseModel):
    """Everything one streaming session produced, with the timings the metrics need. Stored in the cache."""

    events: list[StreamEvent] = Field(default_factory=list)
    audio_seconds: float
    chunk_ms: int
    speed: float
    n_chunks: int = 0
    connect_ms: float = 0.0  # from opening the socket to the first audio byte (handshake, setup)
    audio_end_ms: float = 0.0  # when the audio would have finished playing, since the first audio byte
    end_sent_ms: float = 0.0  # when the end-of-stream message went out
    closed_ms: float = 0.0  # when the session ended
    session_s: float = 0.0  # socket open to session end, what session-billed providers charge for
    timed_out: bool = False  # no terminal message and no quiet period before the final timeout
    meta: dict[str, Any] = Field(default_factory=dict)


class Parsed(NamedTuple):
    kind: EventKind
    segment: str
    text: str
    words: list[Word] | None = None


# ---------------------------------------------------------------- provider


class StreamingProvider(ABC):
    """Base class for a realtime adapter. Prices come from `[<id>]` in providers/pricing.toml."""

    id: ClassVar[str]
    env_keys: ClassVar[tuple[str, ...]] = ()
    languages: ClassVar[frozenset[str]] = ALL_LANGUAGES
    # Clips streamed at once per provider. Every session runs in real time, so this is the throughput knob.
    concurrency: ClassVar[int] = 2
    paid: ClassVar[bool] = True
    uploads_audio: ClassVar[bool] = True
    # "audio": billed per audio minute sent. "session": billed per minute the socket is open.
    billing: ClassVar[str] = "audio"
    # Providers with no terminal message end the session after this many seconds without a message,
    # counted from the end-of-stream message. None: wait for is_terminal() or the server closing.
    quiet_s: ClassVar[float | None] = None
    # Longest session the provider allows; longer clips are skipped.
    max_session_s: ClassVar[float | None] = None
    sample_rate: ClassVar[int] = 16000

    def __init__(self, model_id: str | None = None, **options: Any) -> None:
        self.model_id = model_id or self.default_model_id()
        self.options = options

    # -- configuration

    @classmethod
    def pricing(cls) -> dict[str, Any]:
        return dict(load_pricing().get(cls.id, {}))

    @classmethod
    def default_model_id(cls) -> str:
        return str(cls.pricing().get("model_id", "unknown"))

    def availability(self) -> tuple[bool, str]:
        missing = [k for k in self.env_keys if not os.environ.get(k)]
        if missing:
            return False, f"{self.id}: skipped, missing {', '.join(missing)} in .env"
        return True, "ok"

    def settings(self) -> dict[str, Any]:
        """Everything that changes the output. Hashed into the cache key, with kind=stream."""
        return {"kind": "stream", "model_id": self.model_id, **self.options}

    def settings_hash(self) -> str:
        blob = json.dumps(self.settings(), sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def price_per_minute(self) -> float:
        entry = self.pricing()
        prices = entry.get("models", {})
        if self.model_id in prices:
            return float(prices[self.model_id])
        return float(entry.get("usd_per_minute", 0.0))

    def billed_seconds(self, audio_seconds: float, speed: float = 1.0, session_s: float | None = None) -> float:
        """Seconds the provider bills for one clip. For session billing without a measured session, the
        audio's wall time plus `session_overhead_s` from pricing.toml (default 3 s)."""
        if self.billing == "session":
            if session_s is not None:
                return float(session_s)
            overhead = float(self.pricing().get("session_overhead_s", 3.0))
            return audio_seconds / max(speed, 1e-9) + overhead
        return float(audio_seconds)

    def estimate_cost(self, audio_seconds: float, speed: float = 1.0) -> float:
        minimum = float(self.pricing().get("min_billed_seconds", 0))
        return self.price_per_minute() * max(self.billed_seconds(audio_seconds, speed), minimum) / 60.0

    def actual_cost(self, transcript: StreamTranscript) -> float:
        """Cost of a finished session: the provider's own billed duration when it reports one
        (`meta.billed_audio_s` / `meta.billed_session_s`), else the audio or the measured session length."""
        if self.billing == "session":
            billed = float(transcript.meta.get("billed_session_s", transcript.session_s))
        else:
            billed = float(transcript.meta.get("billed_audio_s", transcript.audio_seconds))
        minimum = float(self.pricing().get("min_billed_seconds", 0))
        return self.price_per_minute() * max(billed, minimum) / 60.0

    def check_clip(self, language: str, audio_seconds: float) -> None:
        """Raise ProviderSkip when this provider cannot stream this clip."""
        base = language.split("-")[0].lower()
        if base not in self.languages:
            raise ProviderSkip(f"{self.id}: {self.model_id} does not stream language {language!r}")
        if self.max_session_s is not None and audio_seconds > self.max_session_s:
            raise ProviderSkip(f"{self.id}: clip is longer than the {self.max_session_s:.0f} s session limit")

    # -- wire protocol

    @abstractmethod
    def url(self, language: str) -> str: ...

    def headers(self) -> dict[str, str]:
        return {}

    async def start(self, conn: Conn, language: str, state: dict[str, Any]) -> None:
        """Send setup messages and wait until the provider is ready for audio. Default: nothing."""
        state.setdefault("meta", {})

    def audio_message(self, chunk: bytes) -> str | bytes:
        """How one PCM chunk goes on the wire. Default: a binary frame."""
        return chunk

    def end_messages(self) -> list[str | bytes]:
        """Messages that tell the provider the audio is over."""
        return []

    def new_state(self) -> dict[str, Any]:
        return {"meta": {}}

    @abstractmethod
    def parse(self, message: dict[str, Any], state: dict[str, Any]) -> list[Parsed]:
        """Events in one decoded server message. Raise StreamError for a provider error."""

    def is_terminal(self, message: dict[str, Any], state: dict[str, Any]) -> bool:
        """True when this message is the provider's last one for the session."""
        return False


async def recv_json(conn: Conn) -> dict[str, Any]:
    """Next JSON message, skipping binary frames."""
    while True:
        raw = await conn.recv()
        if isinstance(raw, bytes):
            try:
                raw = raw.decode()
            except UnicodeDecodeError:
                continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            return msg
