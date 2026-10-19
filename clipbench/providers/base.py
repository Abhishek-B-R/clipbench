"""The one interface every speech-to-text system sits behind."""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import os
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result

PRICING_PATH = Path(__file__).with_name("pricing.toml")


class ProviderUnavailable(Exception):
    """Raised when a provider cannot run (missing key, missing package, no data for a clip)."""


class ProviderSkip(Exception):
    """Raised by transcribe() when this provider has nothing for one clip (e.g. no Wayin output)."""


class ProviderError(Exception):
    """A request failed for good after retries.

    `http_status` is the last HTTP status seen (None for network errors or non HTTP failures) and
    `retries` the number of retries made before giving up; the runner records both on the error row.
    """

    def __init__(self, *args: Any, http_status: int | None = None, retries: int = 0) -> None:
        super().__init__(*args)
        self.http_status = http_status
        self.retries = retries


@dataclass
class CallStats:
    """Retries and last HTTP status of one transcribe() call, filled by the retry helpers."""

    retries: int = 0
    http_status: int | None = None


_call_stats: contextvars.ContextVar[CallStats | None] = contextvars.ContextVar(
    "clipbench_call_stats", default=None
)


def call_stats() -> CallStats | None:
    """The stats of the transcribe() call running in this context, if any."""
    return _call_stats.get()


def _wrap_transcribe(fn: Any) -> Any:
    """Give every transcribe() call its own CallStats and copy them onto the Result or the error."""

    @functools.wraps(fn)
    async def transcribe(self: Provider, *args: Any, **kwargs: Any) -> Result:
        if _call_stats.get() is not None:  # a subclass calling super().transcribe()
            return await fn(self, *args, **kwargs)
        stats = CallStats()
        token = _call_stats.set(stats)
        try:
            result = await fn(self, *args, **kwargs)
        except Exception as exc:
            if getattr(exc, "http_status", None) is None:
                try:
                    exc.http_status = stats.http_status  # type: ignore[attr-defined]
                except AttributeError:  # pragma: no cover - exceptions with __slots__
                    pass
            if not getattr(exc, "retries", 0):
                try:
                    exc.retries = stats.retries  # type: ignore[attr-defined]
                except AttributeError:  # pragma: no cover
                    pass
            raise
        finally:
            _call_stats.reset(token)
        if not result.retries:
            result.retries = stats.retries
        if result.http_status is None:
            result.http_status = stats.http_status
        language = args[1] if len(args) > 1 else kwargs.get("language", "")
        self.record_unsupported(result, language)
        return result

    transcribe._clipbench_wrapped = True  # type: ignore[attr-defined]
    return transcribe


# Run options passed to every provider (docs/V3.md). Adapters list the ones they implement in
# `supported_options`; any other one that is set is recorded in raw_response["clipbench_unsupported"].
RUN_OPTIONS = ("diarize", "detect_language", "keyterms")

# ISO-639-3 and English names some APIs return, mapped to the ISO-639-1 codes clipbench uses.
_LANGUAGE_ALIASES = {
    "eng": "en", "english": "en",
    "jpn": "ja", "japanese": "ja",
    "kor": "ko", "korean": "ko",
    "spa": "es", "spanish": "es",
    "hin": "hi", "hindi": "hi",
}


def normalize_language_code(code: Any) -> str | None:
    """'ja-JP' -> 'ja', 'jpn' -> 'ja', 'Japanese' -> 'ja'. None for empty input."""
    if code is None:
        return None
    text = str(code).strip().replace("_", "-").lower()
    if not text:
        return None
    base = text.split("-")[0]
    return _LANGUAGE_ALIASES.get(base, base)


@lru_cache(maxsize=1)
def load_pricing() -> dict[str, Any]:
    if not PRICING_PATH.exists():
        return {}
    return tomllib.loads(PRICING_PATH.read_text())


class Provider(ABC):
    """Base class for an adapter.

    Subclasses set `id`, `model_id`, `env_keys` and implement `transcribe`.
    `clip` is passed so adapters that read existing data (youtube-auto, wayin) know the video id;
    network adapters only need the audio path.
    """

    id: ClassVar[str]
    env_keys: ClassVar[tuple[str, ...]] = ()
    concurrency: ClassVar[int] = 4
    timeout_s: ClassVar[float] = 120.0
    paid: ClassVar[bool] = True
    # Sends audio to a third party. False for local models and for data already on disk.
    uploads_audio: ClassVar[bool] = True

    # Which run options (RUN_OPTIONS) this adapter implements.
    supported_options: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        fn = cls.__dict__.get("transcribe")
        if fn is not None and not getattr(fn, "__isabstractmethod__", False) and not getattr(
            fn, "_clipbench_wrapped", False
        ):
            cls.transcribe = _wrap_transcribe(fn)  # type: ignore[method-assign]

    def __init__(self, model_id: str | None = None, **options: Any) -> None:
        self.model_id = model_id or self.default_model_id()
        self.options = options

    # ------------------------------------------------------------ run options

    @property
    def diarize(self) -> bool:
        return bool(self.options.get("diarize"))

    @property
    def detect_language(self) -> bool:
        return bool(self.options.get("detect_language"))

    @property
    def keyterms(self) -> list[str]:
        terms = self.options.get("keyterms") or []
        if isinstance(terms, str):
            terms = [terms]
        return [str(t).strip() for t in terms if str(t).strip()]

    def requested_options(self) -> list[str]:
        out = []
        for name in RUN_OPTIONS:
            value = self.keyterms if name == "keyterms" else self.options.get(name)
            if value:
                out.append(name)
        return out

    def unsupported_options(self, language: str) -> list[str]:
        """Requested run options this adapter ignores for `language`. Override for per language or
        per model limits."""
        return [o for o in self.requested_options() if o not in self.supported_options]

    def record_unsupported(self, result: Result, language: str) -> None:
        """Record ignored run options in raw_response["clipbench_unsupported"] (sorted, merged)."""
        names = set(self.unsupported_options(language))
        if not names:
            return
        raw = result.raw_response
        if not isinstance(raw, dict):
            raw = {"response": raw}
        names |= set(raw.get("clipbench_unsupported") or [])
        raw["clipbench_unsupported"] = sorted(names)
        result.raw_response = raw

    @classmethod
    def default_model_id(cls) -> str:
        return str(load_pricing().get(cls.id, {}).get("model_id", "unknown"))

    def job_timeout_s(self) -> float:
        """How long the runner waits for one whole transcribe() call (retries and polling included).
        `timeout_s` is the default; adapters whose timeout_s bounds a single request override this."""
        return float(self.timeout_s)

    def availability(self) -> tuple[bool, str]:
        """(ok, message). Default: every env key must be set."""
        missing = [k for k in self.env_keys if not os.environ.get(k)]
        if missing:
            return False, f"{self.id}: skipped, missing {', '.join(missing)} in .env"
        return True, "ok"

    def settings(self) -> dict[str, Any]:
        """Everything that changes the output. Hashed into the cache key."""
        return {"model_id": self.model_id, **self.options}

    def settings_hash(self) -> str:
        blob = json.dumps(self.settings(), sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def price_per_minute(self) -> float:
        entry = load_pricing().get(self.id, {})
        prices = entry.get("models", {})
        if self.model_id in prices:
            return float(prices[self.model_id])
        return float(entry.get("usd_per_minute", 0.0))

    def estimate_cost(self, audio_seconds: float) -> float:
        entry = load_pricing().get(self.id, {})
        minimum = float(entry.get("min_billed_seconds", 0))
        return self.price_per_minute() * max(audio_seconds, minimum) / 60.0

    @abstractmethod
    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        """Transcribe one file. Must not raise for a skippable clip; raise ProviderSkip instead."""
