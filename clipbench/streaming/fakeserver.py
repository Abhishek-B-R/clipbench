"""An in-memory WebSocket server for tests and offline checks. No socket is ever opened.

`memory_connect(handler)` returns a `Connect` callable. Each connection runs `handler(server, url, headers)`
as a task, where `server` has the same `send` / `recv` / `close` methods as a client connection. Messages
travel through two asyncio queues, so a test can script a provider's exact wire protocol and assert on what
the client sent (`server.received`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from clipbench.streaming.base import Connect, StreamClosed

_CLOSE = object()


@dataclass
class _End:
    queue_in: asyncio.Queue
    queue_out: asyncio.Queue
    closed: dict = field(default_factory=lambda: {"v": False})
    received: list = field(default_factory=list)

    async def send(self, data: str | bytes) -> None:
        if self.closed["v"]:
            raise StreamClosed(1000, "closed")
        await self.queue_out.put(data)

    async def recv(self) -> str | bytes:
        item = await self.queue_in.get()
        if item is _CLOSE:
            self.closed["v"] = True
            await self.queue_in.put(_CLOSE)  # every later recv sees the close too
            raise StreamClosed(1000, "closed")
        self.received.append(item)
        return item

    async def close(self) -> None:
        if not self.closed["v"]:
            self.closed["v"] = True
            await self.queue_out.put(_CLOSE)


Handler = Callable[[_End, str, dict[str, str]], Awaitable[None]]


@dataclass
class MemoryServer:
    handler: Handler
    connections: list[dict] = field(default_factory=list)  # {"url", "headers", "server": _End}
    errors: list[BaseException] = field(default_factory=list)

    def connect(self) -> Connect:
        @asynccontextmanager
        async def _connect(url: str, headers: dict[str, str]) -> AsyncIterator[_End]:
            to_server: asyncio.Queue = asyncio.Queue()
            to_client: asyncio.Queue = asyncio.Queue()
            shared = {"v": False}
            server = _End(queue_in=to_server, queue_out=to_client)
            client = _End(queue_in=to_client, queue_out=to_server, closed=shared)
            server.closed = shared
            self.connections.append({"url": url, "headers": dict(headers), "server": server})

            async def run() -> None:
                try:
                    await self.handler(server, url, headers)
                except StreamClosed:
                    pass
                except BaseException as exc:  # noqa: BLE001 - surfaced to the test
                    self.errors.append(exc)
                    raise
                finally:
                    await to_client.put(_CLOSE)

            task = asyncio.create_task(run())
            try:
                yield client
            finally:
                if not shared["v"]:
                    shared["v"] = True
                    await to_server.put(_CLOSE)
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (TimeoutError, StreamClosed):
                    task.cancel()
                except Exception:  # noqa: BLE001 - recorded in self.errors
                    pass

        return _connect


def memory_connect(handler: Handler) -> tuple[Connect, MemoryServer]:
    server = MemoryServer(handler)
    return server.connect(), server
