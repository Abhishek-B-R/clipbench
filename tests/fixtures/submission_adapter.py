"""Example third-party adapters for the submission contract tests (and the CONTRIBUTING.md template).

`ExampleApi` follows the contract: an HTTP adapter whose parser is shared by `transcribe` and the
offline shape check (`fixture_response` is a made-up response for a synthetic tone, not real speech).
The other classes each break one rule.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import Provider, ProviderError


class ExampleApi(Provider):
    id: ClassVar[str] = "example-api"
    env_keys: ClassVar[tuple[str, ...]] = ("EXAMPLE_API_KEY",)
    concurrency: ClassVar[int] = 2
    timeout_s: ClassVar[float] = 5.0
    paid: ClassVar[bool] = True
    uploads_audio: ClassVar[bool] = True

    fixture_response: ClassVar[dict[str, Any]] = {
        "model": "example-1",
        "duration": 2.0,
        "words": [{"w": "tone", "s": 0.10, "e": 0.62}, {"w": "test", "s": 0.70, "e": 1.30}],
    }

    @classmethod
    def default_model_id(cls) -> str:
        return "example-1"

    def price_per_minute(self) -> float:
        return 0.004

    def parse_response(self, raw: Any, audio_seconds: float) -> Result:
        if not isinstance(raw, dict) or "words" not in raw or "duration" not in raw:
            raise ProviderError("unexpected response shape")
        words = [Word(text=w["w"], start_ms=round(w["s"] * 1000), end_ms=round(w["e"] * 1000)) for w in raw["words"]]
        seconds = float(raw["duration"])
        return Result(
            text=" ".join(w.text for w in words),
            words=words,
            latency_ms=0.0,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(seconds),
            raw_response=raw,
            model_id=str(raw.get("model") or self.model_id),
        )

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                "https://api.example.invalid/v1/transcribe",
                params={"language": language, "model": self.model_id},
                headers={"authorization": f"Bearer {os.environ.get('EXAMPLE_API_KEY', '')}"},
                content=Path(audio_path).read_bytes(),
            )
        resp.raise_for_status()
        result = self.parse_response(resp.json(), 0.0)
        result.latency_ms = (time.perf_counter() - t0) * 1000
        return result


class LocalModel(Provider):
    """A local model: no keys, works offline, so transcribe() itself is checked."""

    id: ClassVar[str] = "example-local"
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False

    @classmethod
    def default_model_id(cls) -> str:
        return "example-local-1"

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        import soundfile

        seconds = float(soundfile.info(str(audio_path)).duration)
        return Result(text="", words=[], latency_ms=1.0, audio_seconds=seconds, cost_usd=0.0, model_id=self.model_id)


class SaysAvailableWithoutKeys(ExampleApi):
    id: ClassVar[str] = "example-avail"

    def availability(self) -> tuple[bool, str]:
        return True, "ok"


class BadTimings(ExampleApi):
    id: ClassVar[str] = "example-timings"
    fixture_response: ClassVar[dict[str, Any]] = {
        "model": "example-1",
        "duration": 2.0,
        "words": [{"w": "b", "s": 1.0, "e": 1.2}, {"w": "a", "s": 0.2, "e": 0.1}],
    }


class SwallowsErrors(ExampleApi):
    id: ClassVar[str] = "example-swallow"

    def parse_response(self, raw: Any, audio_seconds: float) -> Result:
        try:
            return super().parse_response(raw, audio_seconds)
        except ProviderError:
            return Result(text="", latency_ms=0.0, audio_seconds=2.0, cost_usd=0.0, model_id=self.model_id)

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        try:
            return await super().transcribe(audio_path, language, clip=clip)
        except Exception:  # noqa: BLE001 - the bug under test
            return Result(text="", latency_ms=0.0, audio_seconds=2.0, cost_usd=0.0, model_id=self.model_id)


class NoFixture(ExampleApi):
    id: ClassVar[str] = "example-nofixture"
    fixture_response = None  # type: ignore[assignment]


class FreeButPaid(ExampleApi):
    id: ClassVar[str] = "example-noprice"

    def price_per_minute(self) -> float:
        return 0.0


class ClashesWithRegistry(ExampleApi):
    id: ClassVar[str] = "deepgram"


class NotAProvider:
    id = "example-notprovider"
