"""ElevenLabs Scribe. https://elevenlabs.io/docs/api-reference/speech-to-text/convert

Run options (checked 2026-10-19 on the page above):
- diarize: `diarize=true`; per word `speaker_id` ("speaker_0").
- detect_language: no `language_code`; the result is `language_code` (ISO-639-3 such as "jpn" or
  ISO-639-1, both normalised to "ja").
- keyterms: `keyterms` form fields, up to 1000 terms, each under 50 characters and at most 5 words,
  none of < > { } [ ] or a backslash. Keyterms add 20% to the price, and more than 100 terms bill at least 20 s.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import normalize_language_code
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds

TRUTHY = {"1", "true", "yes", "on"}
KEYTERM_FORBIDDEN = set("<>{}[]\\")
MAX_KEYTERMS = 1000
KEYTERM_SURCHARGE = 0.20
KEYTERM_MIN_BILLED_S = 20.0  # when more than 100 keyterms are sent


def valid_keyterms(terms: list[str]) -> list[str]:
    out = [
        t for t in terms if len(t) < 50 and len(t.split()) <= 5 and not (set(t) & KEYTERM_FORBIDDEN)
    ]
    return out[:MAX_KEYTERMS]


class ElevenLabsScribe(HttpProvider):
    id: ClassVar[str] = "elevenlabs-scribe"
    env_keys: ClassVar[tuple[str, ...]] = ("ELEVENLABS_API_KEY",)
    concurrency: ClassVar[int] = 4
    timeout_s: ClassVar[float] = 180.0
    base_url: ClassVar[str] = "https://api.elevenlabs.io/v1"
    supported_options: ClassVar[frozenset[str]] = frozenset({"diarize", "detect_language", "keyterms"})

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("tag_audio_events", False)
        kwargs.setdefault("diarize", False)
        super().__init__(model_id, **kwargs)
        # Zero retention mode is Enterprise only; other plans reject enable_logging=false.
        env = os.environ.get("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", "").strip().lower()
        self.zero_retention = bool(kwargs.get("zero_retention", env in TRUTHY))

    def settings(self) -> dict[str, Any]:
        s = super().settings()
        s.pop("zero_retention", None)  # does not change the output
        return s

    def headers(self) -> dict[str, str]:
        return {"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")}

    def form(self, language: str) -> dict[str, Any]:
        form: dict[str, Any] = {"model_id": self.model_id}
        if not self.detect_language:
            form["language_code"] = language  # ISO-639-1: ja, es, ko
        form.update(
            {
                "timestamps_granularity": "word",
                "tag_audio_events": str(bool(self.options["tag_audio_events"])).lower(),
                "diarize": str(bool(self.options["diarize"])).lower(),
            }
        )
        terms = valid_keyterms(self.keyterms)
        if terms:
            form["keyterms"] = terms
        return form

    def estimate_cost(self, audio_seconds: float) -> float:
        terms = valid_keyterms(self.keyterms)
        if not terms:
            return super().estimate_cost(audio_seconds)
        if len(terms) > 100:
            audio_seconds = max(audio_seconds, KEYTERM_MIN_BILLED_S)
        return super().estimate_cost(audio_seconds) * (1 + KEYTERM_SURCHARGE)

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        params = {"enable_logging": "false"} if self.zero_retention else None
        async with self.slot(), stopwatch() as watch:
            resp = await self.http.request(
                "POST",
                "/speech-to-text",
                params=params,
                data=self.form(language),
                files={"file": (audio_path.name, audio, "audio/wav")},
            )
            body = resp.json()
        kept = [
            w for w in body.get("words") or [] if w.get("type", "word") == "word" and w.get("start") is not None
        ]
        words = [
            Word(
                text=w["text"], start_ms=round(float(w["start"]) * 1000), end_ms=round(float(w["end"]) * 1000)
            )
            for w in kept
        ]
        speakers = None
        if self.diarize and any(w.get("speaker_id") is not None for w in kept):
            speakers = [None if w.get("speaker_id") is None else str(w["speaker_id"]) for w in kept]
        return Result(
            text=(body.get("text") or "").strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(seconds),
            raw_response=body,
            model_id=self.model_id,
            provider_version=None,  # the API reports no model version
            speakers=speakers if words else None,
            detected_language=normalize_language_code(body.get("language_code")) if self.detect_language else None,
        )
