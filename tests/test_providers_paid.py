"""Paid adapters against mocked transports. No network, no real sleeps, no keys."""

from __future__ import annotations

import io
import json

import httpx
import numpy as np
import pytest
import soundfile

from clipbench.providers.assemblyai import AssemblyAI
from clipbench.providers.base import ProviderError, ProviderSkip
from clipbench.providers.deepgram import Deepgram
from clipbench.providers.elevenlabs import ElevenLabsScribe
from clipbench.providers.gemini import GeminiFlash, extract_text
from clipbench.providers.groq import GroqWhisper
from clipbench.providers.http import RetryPolicy, no_sleep_policy, parse_retry_after
from clipbench.providers.registry import provider_class

ALL_KEYS = [
    "GROQ_API_KEY",
    "DEEPGRAM_API_KEY",
    "ASSEMBLYAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "GEMINI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "CLIPBENCH_AWS_BUCKET",
    "CLIPBENCH_ELEVENLABS_ZERO_RETENTION",
]


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k in ALL_KEYS[:5]:
        monkeypatch.setenv(k, f"test-{k.lower()}")


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "vid123.wav"
    soundfile.write(str(p), np.zeros(16000 * 3, dtype="float32"), 16000)  # 3 s
    return p


def flaky(handler, failures: int = 1, status: int = 429, headers: dict | None = None):
    """Fail the first `failures` requests with `status`, then defer to handler."""
    state = {"n": 0}

    def wrapped(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] <= failures:
            return httpx.Response(status, headers=headers or {}, json={"error": "slow down"})
        return handler(request)

    wrapped.state = state  # type: ignore[attr-defined]
    return wrapped


def always(status: int):
    return lambda request: httpx.Response(status, json={"error": "nope"})


def make(cls, handler, attempts: int = 3, **kw):
    policy = no_sleep_policy(attempts)
    return cls(transport=httpx.MockTransport(handler), retry=policy, **kw), policy


# ------------------------------------------------------------------ http helper


def test_retry_after_parsing():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def test_backoff_grows_and_caps():
    p = RetryPolicy(base_delay_s=1, max_delay_s=5, jitter=False)
    assert [p.backoff(i) for i in (1, 2, 3, 4)] == [1, 2, 4, 5]
    assert p.backoff(1, retry_after=7) == 7
    assert p.backoff(1, retry_after=10_000) == p.max_retry_after_s


async def test_network_error_is_retried(wav):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"text": "hola", "words": []})

    p, _ = make(GroqWhisper, handler)
    r = await p.transcribe(wav, "es")
    assert r.text == "hola" and calls["n"] == 2


async def test_4xx_is_not_retried(wav):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    p, _ = make(GroqWhisper, handler)
    with pytest.raises(ProviderError, match="401"):
        await p.transcribe(wav, "ja")
    assert calls["n"] == 1


# ------------------------------------------------------------------ groq

GROQ_BODY = {
    "text": " こんにちは世界",
    "words": [{"word": "こんにちは", "start": 0.0, "end": 0.5}, {"word": "世界", "start": 0.5, "end": 1.0}],
    "x_groq": {"id": "req_1"},
}


def groq_handler(seen):
    def h(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=GROQ_BODY)

    return h


async def test_groq_request_and_result(wav):
    seen: list[httpx.Request] = []
    p, _ = make(GroqWhisper, groq_handler(seen))
    r = await p.transcribe(wav, "ja")
    req = seen[0]
    assert str(req.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert req.headers["authorization"] == "Bearer test-groq_api_key"
    body = req.content
    assert b'name="model"\r\n\r\nwhisper-large-v3\r\n' in body
    assert b'name="language"\r\n\r\nja\r\n' in body
    assert b'name="response_format"\r\n\r\nverbose_json' in body
    assert b'name="timestamp_granularities[]"\r\n\r\nword' in body
    assert r.text == "こんにちは世界"
    assert [w.text for w in r.words] == ["こんにちは", "世界"]
    assert r.words[1].start_ms == 500
    assert r.audio_seconds == pytest.approx(3.0)
    # 3 s audio billed at the 10 s minimum at 0.111 USD per hour
    assert r.cost_usd == pytest.approx(0.00185 * 10 / 60)
    assert r.model_id == "whisper-large-v3"


async def test_groq_retry_then_success(wav):
    seen: list = []
    handler = flaky(groq_handler(seen), failures=2, headers={"retry-after": "3"})
    p, policy = make(GroqWhisper, handler)
    r = await p.transcribe(wav, "ja")
    assert r.text == "こんにちは世界"
    assert policy.delays == [3.0, 3.0]  # Retry-After honoured


async def test_groq_fails_after_retries(wav):
    p, policy = make(GroqWhisper, always(503), attempts=3)
    with pytest.raises(ProviderError, match="after 3 attempts"):
        await p.transcribe(wav, "ja")
    assert len(policy.delays) == 2


# ------------------------------------------------------------------ deepgram

DG_BODY = {
    "metadata": {
        "request_id": "r1",
        "duration": 3.0,
        "models": ["uuid-1"],
        "model_info": {"uuid-1": {"name": "general-nova-3", "version": "2026-09-01.0", "arch": "nova-3"}},
    },
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "hola mundo",
                        "words": [
                            {"word": "hola", "punctuated_word": "Hola", "start": 0.1, "end": 0.4},
                            {"word": "mundo", "punctuated_word": "mundo.", "start": 0.5, "end": 0.9},
                        ],
                    }
                ]
            }
        ]
    },
}


async def test_deepgram_request_and_result(wav):
    seen: list[httpx.Request] = []

    def h(request):
        seen.append(request)
        return httpx.Response(200, json=DG_BODY)

    p, _ = make(Deepgram, h)
    r = await p.transcribe(wav, "es")
    req = seen[0]
    assert req.url.path == "/v1/listen"
    q = req.url.params
    assert q["model"] == "nova-3" and q["language"] == "es" and q["mip_opt_out"] == "true"
    assert req.headers["authorization"] == "Token test-deepgram_api_key"
    assert req.headers["content-type"] == "audio/wav"
    assert req.content == wav.read_bytes()
    assert r.text == "hola mundo"
    assert [w.text for w in r.words] == ["Hola", "mundo."]
    assert r.cost_usd == pytest.approx(0.0043 * 3 / 60)
    assert r.provider_version == "general-nova-3:2026-09-01.0"


async def test_deepgram_retry_and_failure(wav):
    p, _ = make(Deepgram, flaky(lambda r: httpx.Response(200, json=DG_BODY), failures=1, status=500))
    assert (await p.transcribe(wav, "es")).text == "hola mundo"
    p, _ = make(Deepgram, always(429))
    with pytest.raises(ProviderError):
        await p.transcribe(wav, "es")


# ------------------------------------------------------------------ assemblyai


def aai_handler(seen, polls: int = 2, final: dict | None = None):
    state = {"gets": 0}
    done = final or {
        "id": "t1",
        "status": "completed",
        "text": "안녕하세요",
        "words": [{"text": "안녕하세요", "start": 100, "end": 900}],
        "audio_duration": 3,
        "speech_model_used": "universal-2",
    }

    def h(request: httpx.Request):
        seen.append(request)
        if request.method == "POST" and request.url.path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/abc"})
        if request.method == "POST" and request.url.path == "/v2/transcript":
            return httpx.Response(200, json={"id": "t1", "status": "queued"})
        if request.method == "GET":
            state["gets"] += 1
            if state["gets"] < polls:
                return httpx.Response(200, json={"id": "t1", "status": "processing"})
            return httpx.Response(200, json=done)
        if request.method == "DELETE":
            return httpx.Response(200, json={"id": "t1"})
        return httpx.Response(404)

    return h


async def test_assemblyai_flow(wav):
    seen: list[httpx.Request] = []
    p, policy = make(AssemblyAI, aai_handler(seen))
    r = await p.transcribe(wav, "ko")
    methods = [(q.method, q.url.path) for q in seen]
    assert methods == [
        ("POST", "/v2/upload"),
        ("POST", "/v2/transcript"),
        ("GET", "/v2/transcript/t1"),
        ("GET", "/v2/transcript/t1"),
        ("DELETE", "/v2/transcript/t1"),
    ]
    submit = json.loads(seen[1].content)
    assert submit["speech_models"] == ["universal-3-5-pro", "universal-2"]
    assert submit["language_code"] == "ko"
    assert submit["audio_url"] == "https://cdn.assemblyai.com/upload/abc"
    assert seen[0].headers["authorization"] == "test-assemblyai_api_key"
    assert r.text == "안녕하세요"
    assert r.words[0].start_ms == 100
    assert r.provider_version == "universal-2"
    assert r.cost_usd == pytest.approx(0.0025 * 3 / 60)  # priced by the model actually used
    assert policy.delays == [p.poll_interval_s, p.poll_interval_s]


async def test_assemblyai_retry_then_success(wav):
    seen: list = []
    p, _ = make(AssemblyAI, flaky(aai_handler(seen), failures=1, status=502))
    assert (await p.transcribe(wav, "ja")).text == "안녕하세요"


async def test_assemblyai_job_error_still_deletes(wav):
    seen: list[httpx.Request] = []
    final = {"id": "t1", "status": "error", "error": "audio too short"}
    p, _ = make(AssemblyAI, aai_handler(seen, polls=1, final=final))
    with pytest.raises(ProviderError, match="audio too short"):
        await p.transcribe(wav, "ja")
    assert seen[-1].method == "DELETE"


async def test_assemblyai_fails_after_retries(wav):
    p, _ = make(AssemblyAI, always(500))
    with pytest.raises(ProviderError):
        await p.transcribe(wav, "ja")


# ------------------------------------------------------------------ elevenlabs

EL_BODY = {
    "language_code": "spa",
    "text": "Hola mundo",
    "words": [
        {"text": "Hola", "type": "word", "start": 0.0, "end": 0.4},
        {"text": " ", "type": "spacing", "start": 0.4, "end": 0.5},
        {"text": "mundo", "type": "word", "start": 0.5, "end": 0.9},
    ],
}


async def test_elevenlabs_request_and_result(wav):
    seen: list[httpx.Request] = []

    def h(request):
        seen.append(request)
        return httpx.Response(200, json=EL_BODY)

    p, _ = make(ElevenLabsScribe, h)
    r = await p.transcribe(wav, "es")
    req = seen[0]
    assert req.url.path == "/v1/speech-to-text"
    assert "enable_logging" not in req.url.params  # Enterprise only, off by default
    assert req.headers["xi-api-key"] == "test-elevenlabs_api_key"
    assert b'name="model_id"\r\n\r\nscribe_v2' in req.content
    assert b'name="language_code"\r\n\r\nes' in req.content
    assert b'name="tag_audio_events"\r\n\r\nfalse' in req.content
    assert r.text == "Hola mundo"
    assert [w.text for w in r.words] == ["Hola", "mundo"]
    assert r.cost_usd == pytest.approx(0.003667 * 3 / 60)


async def test_elevenlabs_zero_retention_flag(wav, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", "1")
    seen: list[httpx.Request] = []

    def h(request):
        seen.append(request)
        return httpx.Response(200, json=EL_BODY)

    p, _ = make(ElevenLabsScribe, h)
    await p.transcribe(wav, "es")
    assert seen[0].url.params["enable_logging"] == "false"
    # the flag must not change the cache key
    assert p.settings_hash() == ElevenLabsScribe().settings_hash()


async def test_elevenlabs_retry_and_failure(wav):
    p, _ = make(ElevenLabsScribe, flaky(lambda r: httpx.Response(200, json=EL_BODY), failures=1))
    assert (await p.transcribe(wav, "es")).text == "Hola mundo"
    p, _ = make(ElevenLabsScribe, always(429))
    with pytest.raises(ProviderError):
        await p.transcribe(wav, "es")


# ------------------------------------------------------------------ gemini

GEM_BODY = {
    "candidates": [{"content": {"parts": [{"text": "こんにちは\n"}]}, "finishReason": "STOP"}],
    "usageMetadata": {
        "promptTokenCount": 196,
        "promptTokensDetails": [
            {"modality": "TEXT", "tokenCount": 100},
            {"modality": "AUDIO", "tokenCount": 96},
        ],
        "candidatesTokenCount": 10,
        "thoughtsTokenCount": 40,
        "totalTokenCount": 246,
    },
    "modelVersion": "gemini-3.8-flash-001",
    "responseId": "abc",
}


async def test_gemini_inline_request_and_cost(wav):
    seen: list[httpx.Request] = []

    def h(request):
        seen.append(request)
        return httpx.Response(200, json=GEM_BODY)

    p, _ = make(GeminiFlash, h)
    r = await p.transcribe(wav, "ja")
    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req.headers["x-goog-api-key"] == "test-gemini_api_key"
    body = json.loads(req.content)
    parts = body["contents"][0]["parts"]
    assert "Japanese" in parts[0]["text"] and "Do not translate" in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "audio/wav"
    assert body["generationConfig"]["temperature"] == 0.0
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert r.text == "こんにちは"
    assert r.words is None
    assert r.provider_version == "gemini-3.8-flash-001"
    expected = (96 * 0.75 + 100 * 0.75 + 50 * 3.75) / 1_000_000
    assert r.cost_usd == pytest.approx(expected)


async def test_gemini_files_api_for_large_audio(wav):
    seen: list[httpx.Request] = []

    def h(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/upload/v1beta/files":
            return httpx.Response(200, headers={"x-goog-upload-url": "https://upload.example/u1"}, json={})
        if request.url.host == "upload.example":
            return httpx.Response(200, json={"file": {"name": "files/f1", "uri": "https://g/files/f1"}})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=GEM_BODY)

    p, _ = make(GeminiFlash, h)
    p.inline_max_bytes = 10
    r = await p.transcribe(wav, "ja")
    assert r.text == "こんにちは"
    gen = json.loads(seen[2].content)
    assert gen["contents"][0]["parts"][1]["file_data"]["file_uri"] == "https://g/files/f1"
    assert seen[-1].method == "DELETE" and seen[-1].url.path == "/v1beta/files/f1"


async def test_gemini_retry_and_failure(wav):
    p, _ = make(GeminiFlash, flaky(lambda r: httpx.Response(200, json=GEM_BODY), failures=1, status=503))
    assert (await p.transcribe(wav, "ja")).text == "こんにちは"
    p, _ = make(GeminiFlash, always(429))
    with pytest.raises(ProviderError):
        await p.transcribe(wav, "ja")


def test_gemini_text_extraction():
    assert extract_text({"candidates": []}) == ""
    body = {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": "hola"}]}}]}
    assert extract_text(body) == "hola"
    assert extract_text({"candidates": [{"content": {"parts": [{"text": "```text\nhola\n```"}]}}]}) == "hola"


# ------------------------------------------------------------------ aws transcribe


@pytest.fixture
def aws(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from botocore.config import Config
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CLIPBENCH_AWS_BUCKET", "clipbench-test")
    cfg = Config(retries={"total_max_attempts": 1})
    s3 = boto3.client("s3", region_name="us-east-1", config=cfg)
    tx = boto3.client("transcribe", region_name="us-east-1", config=cfg)
    return s3, tx, Stubber(s3), Stubber(tx)


AWS_TRANSCRIPT = {
    "jobName": "x",
    "results": {
        "transcripts": [{"transcript": "こんにちは 世界"}],
        "items": [
            {
                "type": "pronunciation",
                "start_time": "0.1",
                "end_time": "0.5",
                "alternatives": [{"content": "こんにちは"}],
            },
            {
                "type": "pronunciation",
                "start_time": "0.5",
                "end_time": "0.9",
                "alternatives": [{"content": "世界"}],
            },
            {"type": "punctuation", "alternatives": [{"content": "。"}]},
        ],
    },
}


def _job(status, reason=None):
    j = {"TranscriptionJobName": "x", "TranscriptionJobStatus": status}
    if reason:
        j["FailureReason"] = reason
    return {"TranscriptionJob": j}


def _stub_cleanup(s3s, txs, job=True):
    from botocore.stub import ANY

    s3s.add_response("delete_object", {}, {"Bucket": "clipbench-test", "Key": ANY})
    s3s.add_response("delete_object", {}, {"Bucket": "clipbench-test", "Key": ANY})
    if job:
        txs.add_response("delete_transcription_job", {}, {"TranscriptionJobName": ANY})


async def test_aws_flow_with_throttle_retry_and_cleanup(wav, aws):
    from botocore.response import StreamingBody
    from botocore.stub import ANY

    from clipbench.providers.aws_transcribe import AwsTranscribe

    s3, tx, s3s, txs = aws
    s3s.add_response("put_object", {}, {"Bucket": "clipbench-test", "Key": ANY, "Body": ANY})
    txs.add_response(
        "start_transcription_job",
        _job("IN_PROGRESS"),
        {
            "TranscriptionJobName": ANY,
            "LanguageCode": "ja-JP",
            "MediaFormat": "wav",
            "Media": {"MediaFileUri": ANY},
            "OutputBucketName": "clipbench-test",
            "OutputKey": ANY,
        },
    )
    txs.add_client_error(
        "get_transcription_job", service_error_code="ThrottlingException", http_status_code=400
    )
    txs.add_response("get_transcription_job", _job("IN_PROGRESS"), {"TranscriptionJobName": ANY})
    txs.add_response("get_transcription_job", _job("COMPLETED"), {"TranscriptionJobName": ANY})
    raw = json.dumps(AWS_TRANSCRIPT).encode()
    s3s.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(raw), len(raw))},
        {"Bucket": "clipbench-test", "Key": ANY},
    )
    _stub_cleanup(s3s, txs)
    policy = no_sleep_policy(3)
    p = AwsTranscribe(retry=policy, s3_client=s3, transcribe_client=tx)
    assert p.availability()[0]
    with s3s, txs:
        r = await p.transcribe(wav, "ja")
        s3s.assert_no_pending_responses()
        txs.assert_no_pending_responses()
    assert r.text == "こんにちは 世界"
    assert [w.text for w in r.words] == ["こんにちは", "世界"]
    assert r.words[0].start_ms == 100
    assert r.cost_usd == pytest.approx(0.024 * 3 / 60)
    assert len(policy.delays) == 2  # one throttle backoff + one poll wait


async def test_aws_failed_job_still_cleans_up(wav, aws):
    from botocore.stub import ANY

    from clipbench.providers.aws_transcribe import AwsTranscribe

    s3, tx, s3s, txs = aws
    s3s.add_response("put_object", {}, {"Bucket": "clipbench-test", "Key": ANY, "Body": ANY})
    txs.add_response("start_transcription_job", _job("IN_PROGRESS"), None)
    txs.add_response("get_transcription_job", _job("FAILED", "bad audio"), None)
    _stub_cleanup(s3s, txs)
    p = AwsTranscribe(retry=no_sleep_policy(2), s3_client=s3, transcribe_client=tx)
    with s3s, txs, pytest.raises(ProviderError, match="bad audio"):
        await p.transcribe(wav, "ja")
    s3s.assert_no_pending_responses()
    txs.assert_no_pending_responses()


async def test_aws_fails_after_retries(wav, aws):
    from clipbench.providers.aws_transcribe import AwsTranscribe

    s3, tx, s3s, txs = aws
    for _ in range(2):
        s3s.add_client_error("put_object", service_error_code="SlowDown", http_status_code=503)
    _stub_cleanup(s3s, txs, job=False)  # job never started, so only S3 cleanup
    p = AwsTranscribe(retry=no_sleep_policy(2), s3_client=s3, transcribe_client=tx)
    with s3s, txs, pytest.raises(ProviderError, match="after 2 attempts"):
        await p.transcribe(wav, "ja")
    s3s.assert_no_pending_responses()


def test_aws_language_codes():
    from clipbench.providers.aws_transcribe import AwsTranscribe

    p = AwsTranscribe(s3_client=object(), transcribe_client=object())
    assert p.language_code("ja") == "ja-JP"
    assert p.language_code("ko") == "ko-KR"
    assert p.language_code("es") == "es-US"
    assert AwsTranscribe(spanish_variant="es-ES").language_code("es") == "es-ES"
    with pytest.raises(ProviderSkip):
        p.language_code("xx")


# ------------------------------------------------------------------ availability and registry

PAID = ["groq-whisper", "deepgram", "assemblyai", "elevenlabs-scribe", "gemini-flash", "aws-transcribe"]


@pytest.mark.parametrize("pid", PAID)
def test_availability_without_keys(pid, monkeypatch):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    p = provider_class(pid)()
    ok, msg = p.availability()
    assert not ok
    assert msg.startswith(f"{pid}: skipped, missing ")
    for key in p.env_keys:
        assert key in msg


@pytest.mark.parametrize("pid", PAID)
def test_registry_ids_and_pricing(pid):
    cls = provider_class(pid)
    assert cls.id == pid
    p = cls()
    assert p.model_id != "unknown"
    assert p.paid and p.uploads_audio
    assert p.price_per_minute() > 0


def test_test_hooks_do_not_change_cache_key():
    a = GroqWhisper()
    b = GroqWhisper(transport=httpx.MockTransport(lambda r: httpx.Response(200)), retry=no_sleep_policy())
    assert a.settings_hash() == b.settings_hash()


def test_aws_availability_without_boto3(monkeypatch):
    """CI has no boto3: missing keys are still named, and with keys set the missing package is."""
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "boto3" else real(name, *a))
    cls = provider_class("aws-transcribe")
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    ok, msg = cls().availability()
    assert not ok and "AWS_ACCESS_KEY_ID" in msg and "boto3" in msg
    for k in cls.env_keys:
        monkeypatch.setenv(k, "x")
    ok, msg = cls().availability()
    assert not ok and msg == "aws-transcribe: skipped, missing boto3 (install the 'aws' extra)"
