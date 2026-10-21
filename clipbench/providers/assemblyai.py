"""AssemblyAI async pre-recorded: upload, submit, poll, delete.

https://www.assemblyai.com/docs/api-reference/transcripts/submit
Latency is submit-to-result: upload + submit + polling, minus our own retry backoff.

Run options (checked 2026-10-19, https://www.assemblyai.com/docs/api-reference/transcripts/submit):
- diarize: `speaker_labels=true`; per word `speaker` letters ("A", "B").
- detect_language: no `language_code`, `language_detection=true`; the result is `language_code`.
- keyterms: `keyterms_prompt` (1000 terms on Universal-3.5 Pro, 200 on Universal-2, at most 6 words
  per phrase). We cap at 200 so the Universal-2 fallback (Korean) accepts the same list.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import ProviderError, load_pricing, normalize_language_code
from clipbench.providers.http import (
    SHAPE_ERRORS,
    HttpProvider,
    expect_object,
    shape_error,
    stopwatch,
    wav_seconds,
)


class AssemblyAI(HttpProvider):
    id: ClassVar[str] = "assemblyai"
    env_keys: ClassVar[tuple[str, ...]] = ("ASSEMBLYAI_API_KEY",)
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 120.0  # per HTTP request
    poll_timeout_s: ClassVar[float] = 900.0  # whole job
    poll_interval_s: ClassVar[float] = 1.0
    base_url: ClassVar[str] = "https://api.assemblyai.com/v2"
    supported_options: ClassVar[frozenset[str]] = frozenset({"diarize", "detect_language", "keyterms"})
    max_keyterms: ClassVar[int] = 200
    max_keyterm_words: ClassVar[int] = 6

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("fallback_models", ["universal-2"])
        kwargs.setdefault("delete_after", True)
        super().__init__(model_id, **kwargs)

    def headers(self) -> dict[str, str]:
        return {"authorization": os.environ.get("ASSEMBLYAI_API_KEY", "")}

    def job_timeout_s(self) -> float:
        """Upload and submit (with retries), then up to poll_timeout_s of polling, then the delete."""
        return super().job_timeout_s() + float(self.poll_timeout_s)

    def settings(self) -> dict[str, Any]:
        s = super().settings()
        s.pop("delete_after", None)  # does not change the output
        return s

    def request_body(self, upload_url: str, language: str) -> dict[str, Any]:
        models = [self.model_id, *[m for m in self.options["fallback_models"] if m != self.model_id]]
        body: dict[str, Any] = {
            "audio_url": upload_url,
            "speech_models": models,
            "punctuate": True,
            "format_text": True,
        }
        if self.detect_language:
            body["language_detection"] = True
        else:
            body["language_code"] = language  # ja, es, ko (ko is served by universal-2)
        if self.diarize:
            body["speaker_labels"] = True
        terms = [t for t in self.keyterms if len(t.split()) <= self.max_keyterm_words]
        if terms:
            body["keyterms_prompt"] = terms[: self.max_keyterms]
        return body

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        transcript_id: str | None = None
        async with self.slot(), stopwatch() as watch:
            try:
                up = expect_object(self.id, await self.http.json(
                    "POST", "/upload", content=audio, headers={"content-type": "application/octet-stream"}
                ))  # fmt: skip
                if not up.get("upload_url"):
                    raise shape_error(self.id, up)
                job = expect_object(self.id, await self.http.json(
                    "POST", "/transcript", json=self.request_body(up["upload_url"], language)
                ))  # fmt: skip
                if not job.get("id"):
                    raise shape_error(self.id, job)
                transcript_id = str(job["id"])
                deadline = time.monotonic() + self.poll_timeout_s
                while job.get("status") not in ("completed", "error"):
                    if time.monotonic() > deadline:
                        raise ProviderError(f"assemblyai: transcript {transcript_id} timed out while polling")
                    await self.sleep_poll()
                    job = expect_object(self.id, await self.http.json("GET", f"/transcript/{transcript_id}"))
                latency = watch.ms
            finally:
                if transcript_id and self.options.get("delete_after"):
                    await self._delete(transcript_id)
        if job["status"] == "error":
            raise ProviderError(f"assemblyai: transcript {transcript_id} failed: {job.get('error')}")
        used = job.get("speech_model_used") or self.model_id
        raw_words = job.get("words") or []
        try:
            words = [Word(text=w["text"], start_ms=int(w["start"]), end_ms=int(w["end"])) for w in raw_words]
        except SHAPE_ERRORS as e:
            raise shape_error(self.id, job, e) from e
        speakers = None
        if self.diarize and any(w.get("speaker") is not None for w in raw_words):
            speakers = [None if w.get("speaker") is None else str(w["speaker"]) for w in raw_words]
        billed = float(job.get("audio_duration") or seconds)
        return Result(
            text=(job.get("text") or "").strip(),
            words=words or None,
            latency_ms=latency,
            audio_seconds=seconds,
            cost_usd=self.cost_for(used, billed),
            raw_response=job,
            model_id=self.model_id,
            provider_version=used,
            speakers=speakers if words else None,
            detected_language=normalize_language_code(job.get("language_code")) if self.detect_language else None,
        )

    def cost_for(self, model_used: str, seconds: float) -> float:
        prices = load_pricing().get(self.id, {}).get("models", {})
        per_min = float(prices.get(model_used, self.price_per_minute()))
        return per_min * seconds / 60.0

    async def _delete(self, transcript_id: str) -> None:
        """Best effort: removes the transcript and its uploaded audio from AssemblyAI."""
        try:
            await self.http.request("DELETE", f"/transcript/{transcript_id}")
        except ProviderError:
            pass
