"""Stream one clip through one provider and record every partial and final with the time it arrived.

Timeline (all on the pacer's clock, in ms since the first audio byte was sent):

    connect -> start() handshake -> first chunk (t = 0) ... last chunk -> audio_end -> end messages
            -> finals keep arriving -> terminal message / quiet period / server close (closed_ms)

The sender (pacer) and the receiver run at the same time. The session ends when the provider sends its
terminal message, the server closes the socket after the end of the stream, or (providers with `quiet_s`)
nothing arrives for `quiet_s` seconds after the end-of-stream message. If none of that happens within
`final_timeout_s` of the end of the stream, the transcript is kept with `timed_out = True`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from clipbench.streaming.base import (
    Connect,
    StreamClosed,
    StreamError,
    StreamEvent,
    StreamingProvider,
    StreamTranscript,
    websockets_connect,
)
from clipbench.streaming.pacer import Pacer, pcm_seconds

FINAL_TIMEOUT_S = 30.0
POLL_S = 0.05


def _decode(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode()
        except UnicodeDecodeError:
            return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


async def stream_audio(
    provider: StreamingProvider,
    pcm: bytes,
    language: str,
    *,
    pacer: Pacer | None = None,
    connect: Connect | None = None,
    final_timeout_s: float = FINAL_TIMEOUT_S,
    quiet_s: float | None = None,
) -> StreamTranscript:
    """Run one streaming session. `connect` defaults to a real WebSocket; tests pass the in-memory server."""
    pacer = pacer or Pacer()
    connect = connect or websockets_connect
    clock = pacer.clock
    quiet = provider.quiet_s if quiet_s is None else quiet_s
    loop = asyncio.get_running_loop()

    state = provider.new_state()
    events: list[StreamEvent] = []
    first_send: list[float] = []
    end_sent_loop: list[float] = []  # loop time, for the quiet and final timeouts
    end_sent_clock: list[float] = []
    out = StreamTranscript(
        audio_seconds=pcm_seconds(pcm, pacer.sample_rate), chunk_ms=pacer.chunk_ms, speed=pacer.speed
    )

    def rel_ms(t: float) -> float:
        return (t - first_send[0]) * 1000.0 if first_send else 0.0

    opened = clock.now()
    async with connect(provider.url(language), provider.headers()) as conn:
        await provider.start(conn, language, state)

        async def send_chunk(chunk: bytes) -> None:
            if not first_send:
                first_send.append(clock.now())
            await conn.send(provider.audio_message(chunk))

        async def sender():
            log = await pacer.play(pcm, send_chunk)
            for message in provider.end_messages():
                await conn.send(message)
            end_sent_clock.append(clock.now())
            end_sent_loop.append(loop.time())
            return log

        async def receiver() -> None:
            last_msg = loop.time()
            while True:
                try:
                    raw = await asyncio.wait_for(conn.recv(), timeout=POLL_S)
                except TimeoutError:
                    if end_sent_loop:
                        now = loop.time()
                        if quiet is not None and now - max(last_msg, end_sent_loop[0]) >= quiet:
                            return
                        if now - end_sent_loop[0] >= final_timeout_s:
                            out.timed_out = True
                            return
                    continue
                except StreamClosed as exc:
                    if end_sent_loop:
                        return
                    raise StreamError(f"{provider.id}: server closed the stream before the audio ended: {exc}") from exc
                last_msg = loop.time()
                t = clock.now()
                msg = _decode(raw)
                if msg is None:
                    continue
                for p in provider.parse(msg, state):
                    if p.kind == "partial" and not p.text.strip():
                        continue
                    events.append(
                        StreamEvent(t_ms=rel_ms(t), kind=p.kind, segment=p.segment, text=p.text, words=p.words)
                    )
                if provider.is_terminal(msg, state):
                    return

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            done, _ = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in (recv_task, send_task):
                if task in done and task.exception() is not None:
                    raise task.exception()  # type: ignore[misc]
            if not send_task.done():
                # The receiver finished first: the provider ended the session while we were still sending.
                raise StreamError(f"{provider.id}: provider ended the session before the audio was sent")
            log = send_task.result()
            await recv_task
        finally:
            for task in (send_task, recv_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)
        closed = clock.now()
        try:
            await conn.close()
        except Exception:  # noqa: BLE001 - closing a dead socket is fine
            pass

    out.events = events
    out.n_chunks = len(log.sent_at)
    out.connect_ms = (log.start - opened) * 1000.0
    out.audio_end_ms = rel_ms(log.audio_end)
    out.end_sent_ms = rel_ms(end_sent_clock[0]) if end_sent_clock else out.audio_end_ms
    out.closed_ms = rel_ms(closed)
    out.session_s = max(closed - opened, 0.0)
    out.meta = dict(state.get("meta") or {})
    return out
