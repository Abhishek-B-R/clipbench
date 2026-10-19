"""Amazon Transcribe batch via S3.

Upload the WAV to CLIPBENCH_AWS_BUCKET, start a job that writes its JSON back to the same bucket,
poll, read the transcript, then delete the S3 objects and the job (always, in a finally block).
boto3 is sync, so every call runs in asyncio.to_thread.
https://docs.aws.amazon.com/transcribe/latest/dg/batch-med-transcription.html
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from clipbench.models import Clip, Result, Word
from clipbench.providers.base import Provider, ProviderError, ProviderSkip
from clipbench.providers.http import RetryPolicy, retry_async, stopwatch, wav_seconds

LANGUAGE_CODES = {"ja": "ja-JP", "es": "es-US", "ko": "ko-KR", "en": "en-US"}

RETRYABLE_CODES = {
    "ThrottlingException",
    "LimitExceededException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "SlowDown",
    "InternalFailureException",
    "InternalServerError",
    "InternalError",
    "ServiceUnavailable",
    "ServiceUnavailableException",
}


def _is_retryable(e: BaseException) -> bool:
    try:
        from botocore.exceptions import ClientError, ConnectionError, EndpointConnectionError
    except ImportError:  # pragma: no cover
        return False
    if isinstance(e, ConnectionError | EndpointConnectionError):
        return True
    if isinstance(e, ClientError):
        return e.response.get("Error", {}).get("Code") in RETRYABLE_CODES
    return False


class AwsTranscribe(Provider):
    id: ClassVar[str] = "aws-transcribe"
    env_keys: ClassVar[tuple[str, ...]] = (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "CLIPBENCH_AWS_BUCKET",
    )
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 900.0  # whole job, including queueing at AWS
    poll_interval_s: ClassVar[float] = 2.0
    key_prefix: ClassVar[str] = "clipbench-tmp"

    def __init__(
        self,
        model_id: str | None = None,
        *,
        retry: RetryPolicy | None = None,
        s3_client: Any = None,
        transcribe_client: Any = None,
        **options: Any,
    ) -> None:
        options.setdefault("spanish_variant", "es-US")
        super().__init__(model_id, **options)
        self.retry = retry or RetryPolicy()
        self._s3 = s3_client
        self._tx = transcribe_client
        self._sem: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def availability(self) -> tuple[bool, str]:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False, f"{self.id}: skipped, missing boto3 (install the 'aws' extra)"
        return super().availability()

    def language_code(self, language: str) -> str:
        if language == "es":
            return str(self.options["spanish_variant"])
        if language not in LANGUAGE_CODES:
            raise ProviderSkip(f"{self.id}: no Amazon Transcribe language code for {language!r}")
        return LANGUAGE_CODES[language]

    @property
    def bucket(self) -> str:
        return os.environ.get("CLIPBENCH_AWS_BUCKET", "")

    def clients(self) -> tuple[Any, Any]:
        if self._s3 is None or self._tx is None:
            import boto3
            from botocore.config import Config

            region = os.environ.get("AWS_REGION")
            cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=60)
            if self._s3 is None:
                self._s3 = boto3.client("s3", region_name=region, config=cfg)
            if self._tx is None:
                self._tx = boto3.client("transcribe", region_name=region, config=cfg)
        return self._s3, self._tx

    def _slot(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem = asyncio.Semaphore(max(1, self.concurrency))
            self._loop = loop
        return self._sem

    async def _call(self, label: str, fn, /, **kwargs: Any) -> Any:
        return await retry_async(
            lambda: asyncio.to_thread(fn, **kwargs),
            self.retry,
            label=f"{self.id}: {label}",
            is_retryable=_is_retryable,
        )

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        audio_path = Path(audio_path)
        lang = self.language_code(language)
        seconds = wav_seconds(audio_path)
        audio = audio_path.read_bytes()
        s3, tx = self.clients()
        bucket = self.bucket
        token = uuid.uuid4().hex
        job_name = f"clipbench-{token}"
        in_key = f"{self.key_prefix}/{token}/{audio_path.name}"
        out_key = f"{self.key_prefix}/{token}/transcript.json"
        started = False
        async with self._slot(), stopwatch() as watch:
            try:
                await self._call("put_object", s3.put_object, Bucket=bucket, Key=in_key, Body=audio)
                await self._call(
                    "start_transcription_job",
                    tx.start_transcription_job,
                    TranscriptionJobName=job_name,
                    LanguageCode=lang,
                    MediaFormat="wav",
                    Media={"MediaFileUri": f"s3://{bucket}/{in_key}"},
                    OutputBucketName=bucket,
                    OutputKey=out_key,
                )
                started = True
                deadline = time.monotonic() + self.timeout_s
                while True:
                    resp = await self._call(
                        "get_transcription_job", tx.get_transcription_job, TranscriptionJobName=job_name
                    )
                    job = resp["TranscriptionJob"]
                    status = job["TranscriptionJobStatus"]
                    if status == "COMPLETED":
                        break
                    if status == "FAILED":
                        raise ProviderError(f"{self.id}: job {job_name} failed: {job.get('FailureReason')}")
                    if time.monotonic() > deadline:
                        raise ProviderError(
                            f"{self.id}: job {job_name} timed out after {self.timeout_s:.0f}s"
                        )
                    await self.retry.sleep(self.poll_interval_s)
                obj = await self._call("get_object", s3.get_object, Bucket=bucket, Key=out_key)
                payload = await asyncio.to_thread(obj["Body"].read)
                latency = watch.ms
            finally:
                await self._cleanup(s3, tx, bucket, [in_key, out_key], job_name if started else None)
        body = json.loads(payload)
        return parse_transcript(body, self, seconds, latency)

    async def _cleanup(self, s3: Any, tx: Any, bucket: str, keys: list[str], job_name: str | None) -> None:
        """Best effort; never masks the real error."""
        for key in keys:
            try:
                await asyncio.to_thread(s3.delete_object, Bucket=bucket, Key=key)
            except Exception:  # noqa: BLE001
                pass
        if job_name:
            try:
                await asyncio.to_thread(tx.delete_transcription_job, TranscriptionJobName=job_name)
            except Exception:  # noqa: BLE001
                pass


def parse_transcript(
    body: dict[str, Any], provider: AwsTranscribe, seconds: float, latency_ms: float
) -> Result:
    results = body.get("results") or {}
    transcripts = results.get("transcripts") or [{}]
    text = " ".join(t.get("transcript", "") for t in transcripts).strip()
    words: list[Word] = []
    for item in results.get("items") or []:
        if item.get("type") != "pronunciation" or "start_time" not in item:
            continue
        alts = item.get("alternatives") or [{}]
        words.append(
            Word(
                text=alts[0].get("content", ""),
                start_ms=round(float(item["start_time"]) * 1000),
                end_ms=round(float(item["end_time"]) * 1000),
            )
        )
    return Result(
        text=text,
        words=words or None,
        latency_ms=latency_ms,
        audio_seconds=seconds,
        cost_usd=provider.estimate_cost(seconds),
        raw_response=body,
        model_id=provider.model_id,
        provider_version=None,
    )
