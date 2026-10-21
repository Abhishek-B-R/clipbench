"""A provider for tests: returns the reference with seeded, controllable errors.

Error rates are per token of the reference (characters for unspaced languages, words otherwise).
The same (seed, clip) always yields the same output, so tests are deterministic.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import ClassVar

from clipbench.models import Clip, Result
from clipbench.providers.base import Provider, ProviderSkip

UNSPACED = {"ja", "zh", "th"}


class FakeProvider(Provider):
    id: ClassVar[str] = "fake"
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False
    # Test double: subclasses that set paid=True (budget tests) stay outside the verification gate.
    verification_exempt: ClassVar[bool] = True

    def __init__(
        self,
        model_id: str | None = "fake-1",
        *,
        sub_rate: float = 0.05,
        del_rate: float = 0.02,
        ins_rate: float = 0.02,
        seed: int = 0,
        cost_per_minute: float = 0.0,
        latency_ms: float = 50.0,
        hallucinate: str | None = None,
    ) -> None:
        super().__init__(
            model_id,
            sub_rate=sub_rate,
            del_rate=del_rate,
            ins_rate=ins_rate,
            seed=seed,
            cost_per_minute=cost_per_minute,
            hallucinate=hallucinate,
        )
        self.latency_ms = latency_ms
        self.calls = 0

    def price_per_minute(self) -> float:
        return float(self.options["cost_per_minute"])

    def corrupt(self, text: str, language: str, key: str) -> str:
        o = self.options
        seed = int(hashlib.sha256(f"{o['seed']}:{key}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        unspaced = language in UNSPACED
        tokens = list(text) if unspaced else text.split()
        alphabet = list("あいうえおかきくけこ") if unspaced else ["foo", "bar", "baz", "qux"]
        out: list[str] = []
        for tok in tokens:
            r = rng.random()
            if r < o["del_rate"]:
                continue
            if r < o["del_rate"] + o["sub_rate"]:
                out.append(rng.choice(alphabet))
            else:
                out.append(tok)
            if rng.random() < o["ins_rate"]:
                out.append(rng.choice(alphabet))
        if o.get("hallucinate"):
            out.append(o["hallucinate"])
        return ("" if unspaced else " ").join(out)

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is None:
            raise ProviderSkip("fake provider needs the clip to read its reference")
        self.calls += 1
        duration = clip.duration_s
        text = self.corrupt(clip.reference.text, language, clip.audio_sha256)
        return Result(
            text=text,
            latency_ms=self.latency_ms,
            audio_seconds=duration,
            cost_usd=self.estimate_cost(duration),
            raw_response={"fake": True},
            model_id=self.model_id,
            provider_version="fake",
        )
