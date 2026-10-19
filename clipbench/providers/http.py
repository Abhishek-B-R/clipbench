"""Shared plumbing for the paid HTTP adapters.

- `RetryPolicy`: exponential backoff with full jitter on 429, 5xx and network errors, honouring
  Retry-After. The sleep function is injectable so tests never wait for real.
- `HttpProvider`: a Provider base that owns one httpx.AsyncClient and one asyncio.Semaphore per
  event loop, sized from `Provider.concurrency` and `Provider.timeout_s`.
- `Stopwatch`: wall-clock latency of a transcription that excludes our own backoff sleeps and
  queueing, but includes everything the provider made us wait for (upload, polling, the request).
"""

from __future__ import annotations

import asyncio
import contextvars
import email.utils
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
import soundfile

from clipbench.providers.base import Provider, ProviderError

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    max_retry_after_s: float = 120.0
    jitter: bool = True
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    rng: random.Random = field(default_factory=random.Random)

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Delay before retry number `attempt` (1-based)."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_retry_after_s)
        cap = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return self.rng.uniform(0, cap) if self.jitter else cap


def no_sleep_policy(max_attempts: int = 3) -> RetryPolicy:
    """For tests: retries happen instantly and the delays are recorded in `.delays`."""
    delays: list[float] = []

    async def _sleep(s: float) -> None:
        delays.append(s)

    policy = RetryPolicy(max_attempts=max_attempts, sleep=_sleep, jitter=False)
    policy.delays = delays  # type: ignore[attr-defined]
    return policy


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


# ---------------------------------------------------------------- latency


class Stopwatch:
    """Wall-clock time minus time spent in our own retry backoff."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.excluded_s = 0.0

    @property
    def ms(self) -> float:
        return max(time.perf_counter() - self.start - self.excluded_s, 0.0) * 1000.0


_current_watch: contextvars.ContextVar[Stopwatch | None] = contextvars.ContextVar(
    "clipbench_stopwatch", default=None
)


@asynccontextmanager
async def stopwatch():
    watch = Stopwatch()
    token = _current_watch.set(watch)
    try:
        yield watch
    finally:
        _current_watch.reset(token)


async def _backoff_sleep(policy: RetryPolicy, delay: float) -> None:
    watch = _current_watch.get()
    t0 = time.perf_counter()
    await policy.sleep(delay)
    if watch is not None:
        watch.excluded_s += time.perf_counter() - t0


# ---------------------------------------------------------------- retry helpers


def _snippet(resp: httpx.Response, limit: int = 300) -> str:
    try:
        text = resp.text
    except Exception:  # noqa: BLE001 - body may be undecodable
        return "<unreadable body>"
    return text[:limit].replace("\n", " ")


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    label: str,
    is_retryable: Callable[[BaseException], bool],
) -> T:
    """Generic retry loop (used for boto3 calls wrapped in to_thread)."""
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            if not is_retryable(e):
                raise ProviderError(f"{label}: {type(e).__name__}: {e}") from e
            last = e
            if attempt < policy.max_attempts:
                await _backoff_sleep(policy, policy.backoff(attempt))
    raise ProviderError(f"{label}: failed after {policy.max_attempts} attempts: {last}") from last


class HttpClient:
    """Thin wrapper over httpx.AsyncClient that retries and raises ProviderError."""

    def __init__(self, label: str, client: httpx.AsyncClient, policy: RetryPolicy) -> None:
        self.label = label
        self.client = client
        self.policy = policy

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send with retries. Returns a 2xx response or raises ProviderError.

        Bodies passed as `content=bytes`, `data=` and `files=` with bytes values are safe to resend.
        """
        last_err = ""
        for attempt in range(1, self.policy.max_attempts + 1):
            retry_after: float | None = None
            try:
                resp = await self.client.request(method, url, **kwargs)
            except httpx.TransportError as e:  # connect/read errors and timeouts
                last_err = f"{type(e).__name__}: {e}"
            else:
                if resp.is_success:
                    return resp
                last_err = f"HTTP {resp.status_code}: {_snippet(resp)}"
                if resp.status_code not in RETRY_STATUS:
                    raise ProviderError(f"{self.label}: {method} {url} -> {last_err}")
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
            if attempt < self.policy.max_attempts:
                await _backoff_sleep(self.policy, self.policy.backoff(attempt, retry_after))
        raise ProviderError(
            f"{self.label}: {method} {url} failed after {self.policy.max_attempts} attempts: {last_err}"
        )

    async def json(self, method: str, url: str, **kwargs: Any) -> Any:
        resp = await self.request(method, url, **kwargs)
        try:
            return resp.json()
        except ValueError as e:
            raise ProviderError(f"{self.label}: non-JSON response: {_snippet(resp)}") from e


# ---------------------------------------------------------------- provider base


def wav_seconds(path: Path) -> float:
    return float(soundfile.info(str(path)).duration)


class HttpProvider(Provider):
    """Base for adapters that talk HTTP. Subclasses implement `headers()` and `transcribe()`.

    `transport` and `retry` are test hooks and are deliberately kept out of `options`
    so they never enter the cache key.
    """

    base_url: ClassVar[str] = ""
    poll_interval_s: ClassVar[float] = 1.0

    def __init__(
        self,
        model_id: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry: RetryPolicy | None = None,
        **options: Any,
    ) -> None:
        super().__init__(model_id, **options)
        self.transport = transport
        self.retry = retry or RetryPolicy()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: HttpClient | None = None
        self._sem: asyncio.Semaphore | None = None

    def headers(self) -> dict[str, str]:
        return {}

    def _ensure(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._client is None:
            raw = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"user-agent": "clipbench/0.1", **self.headers()},
                timeout=httpx.Timeout(self.timeout_s, connect=min(30.0, self.timeout_s)),
                transport=self.transport,
            )
            self._client = HttpClient(self.id, raw, self.retry)
            self._sem = asyncio.Semaphore(max(1, int(self.concurrency)))
            self._loop = loop

    @property
    def http(self) -> HttpClient:
        self._ensure()
        assert self._client is not None
        return self._client

    @asynccontextmanager
    async def slot(self):
        """Hold one of `concurrency` slots for a whole transcription (submit, poll, fetch)."""
        self._ensure()
        assert self._sem is not None
        async with self._sem:
            yield

    async def sleep_poll(self, seconds: float | None = None) -> None:
        """Polling wait. Counted in latency (the provider made us wait). Uses the retry sleep hook."""
        await self.retry.sleep(self.poll_interval_s if seconds is None else seconds)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.client.aclose()
            self._client = None
