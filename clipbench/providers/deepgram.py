"""Deepgram pre-recorded. https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded

Run options (checked 2026-10-19):
- diarize: `diarize_model=v2` (batch diarizer; `diarize=true` is deprecated and pins v1). Per word
  `speaker` integers. https://developers.deepgram.com/docs/diarization
- detect_language: `detect_language=true` and no `language`; the result is
  `results.channels[0].detected_language`. https://developers.deepgram.com/docs/language-detection
- keyterms: repeated `keyterm` query parameters (Nova-3 keyterm prompting, 500 tokens per request).
  https://developers.deepgram.com/docs/keyterm
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import normalize_language_code
from clipbench.providers.http import HttpProvider, stopwatch, wav_seconds

# Deepgram recommends 20 to 50 keyterms and caps a request at 500 tokens; 100 terms is its stated maximum.
MAX_KEYTERMS = 100


def parse_alternative(body: dict[str, Any]) -> tuple[dict[str, Any], list[Word], list[str | None] | None, str | None]:
    """(alternative, words, speakers, detected_language) from a Deepgram shaped response."""
    channel = body["results"]["channels"][0]
    alt = channel["alternatives"][0]
    raw_words = alt.get("words") or []
    words = [
        Word(
            text=w.get("punctuated_word") or w["word"],
            start_ms=round(float(w["start"]) * 1000),
            end_ms=round(float(w["end"]) * 1000),
        )
        for w in raw_words
    ]
    speakers: list[str | None] | None = None
    if any("speaker" in w for w in raw_words):
        speakers = [None if w.get("speaker") is None else str(w["speaker"]) for w in raw_words]
    return alt, words, speakers, normalize_language_code(channel.get("detected_language"))


def deepgram_option_params(provider: Any, language: str) -> dict[str, Any]:
    """Language, diarization and keyterm parameters shared by Deepgram and Workers AI nova-3."""
    out: dict[str, Any] = {}
    if provider.detect_language:
        out["detect_language"] = "true"
    else:
        out["language"] = language
    if provider.diarize:
        out["diarize_model"] = str(provider.options.get("diarize_model", "v2"))
    if provider.keyterms:
        out["keyterm"] = provider.keyterms[:MAX_KEYTERMS]
    return out


class Deepgram(HttpProvider):
    id: ClassVar[str] = "deepgram"
    env_keys: ClassVar[tuple[str, ...]] = ("DEEPGRAM_API_KEY",)
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 120.0
    base_url: ClassVar[str] = "https://api.deepgram.com/v1"
    supported_options: ClassVar[frozenset[str]] = frozenset({"diarize", "detect_language", "keyterms"})

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("smart_format", True)
        kwargs.setdefault("punctuate", True)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Token {os.environ.get('DEEPGRAM_API_KEY', '')}"}

    def params(self, language: str) -> dict[str, Any]:
        return {
            "model": self.model_id,
            **deepgram_option_params(self, language),  # language (ja, es, ko) or detect_language
            "smart_format": str(bool(self.options["smart_format"])).lower(),
            "punctuate": str(bool(self.options["punctuate"])).lower(),
            "mip_opt_out": "true",  # out of the Model Improvement Program
        }

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        async with self.slot(), stopwatch() as watch:
            resp = await self.http.request(
                "POST",
                "/listen",
                params=self.params(language),
                content=audio,
                headers={"content-type": "audio/wav"},
            )
            body = resp.json()
        alt, words, speakers, detected = parse_alternative(body)
        meta = body.get("metadata") or {}
        billed = float(meta.get("duration") or seconds)
        infos = list((meta.get("model_info") or {}).values())
        version = None
        if infos:
            version = f"{infos[0].get('name', '')}:{infos[0].get('version', '')}".strip(":")
        return Result(
            text=(alt.get("transcript") or "").strip(),
            words=words or None,
            latency_ms=watch.ms,
            audio_seconds=seconds,
            cost_usd=self.estimate_cost(billed),
            raw_response=body,
            model_id=self.model_id,
            provider_version=version,
            speakers=speakers if words else None,
            detected_language=detected if self.detect_language else None,
        )
