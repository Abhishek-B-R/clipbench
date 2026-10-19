"""The one interface every speech-to-text system sits behind."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from abc import ABC, abstractmethod
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
    """A request failed for good after retries."""


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

    def __init__(self, model_id: str | None = None, **options: Any) -> None:
        self.model_id = model_id or self.default_model_id()
        self.options = options

    @classmethod
    def default_model_id(cls) -> str:
        return str(load_pricing().get(cls.id, {}).get("model_id", "unknown"))

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
