"""Cloudflare Workers AI adapters against mocked transports. No network, no real sleeps, no keys."""

from __future__ import annotations

import base64
import io
import json

import httpx
import numpy as np
import pytest
import soundfile

from clipbench.providers.base import ProviderError, ProviderSkip
from clipbench.providers.cloudflare import (
    CfDeepgramNova3,
    CfWhisper,
    CfWhisperLargeV3Turbo,
    CfWhisperTinyEn,
)
from clipbench.providers.http import no_sleep_policy
from clipbench.providers.registry import provider_class

ACCOUNT = "acct123"
TOKEN = "test-cf-token"
RUN = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/"


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "vid123.wav"
    soundfile.write(str(p), np.zeros(16000 * 3, dtype="float32"), 16000)  # 3 s
    return p


def envelope(result: dict, **extra) -> dict:
    return {"result": result, "success": True, "errors": [], "messages": [], **extra}


def recorder(seen: list, body: dict):
    def h(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return h


def make(cls, handler, attempts: int = 3, **kw):
    policy = no_sleep_policy(attempts)
    return cls(transport=httpx.MockTransport(handler), retry=policy, **kw), policy


TURBO = envelope(
    {
        "transcription_info": {"language": "ja", "duration": 3.0},
        "text": "こんにちは世界",
        "word_count": 2,
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "こんにちは世界",
                "words": [
                    {"word": "こんにちは", "start": 0.0, "end": 0.5},
                    {"word": "世界", "start": 0.5, "end": 1.0},
                ],
            }
        ],
        "vtt": "WEBVTT",
    }
)

WHISPER = envelope(
    {
        "text": "hola mundo",
        "word_count": 2,
        "words": [{"word": "hola", "start": 0.1, "end": 0.4}, {"word": "mundo", "start": 0.5, "end": 0.9}],
    }
)

NOVA = envelope(
    {
        "metadata": {"model_info": {"u1": {"name": "general-nova-3", "version": "2026-09-01.0"}}},
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "नमस्ते दुनिया",
                            "words": [
                                {"word": "नमस्ते", "punctuated_word": "नमस्ते", "start": 0.2, "end": 0.6},
                                {"word": "दुनिया", "punctuated_word": "दुनिया।", "start": 0.7, "end": 1.1},
                            ],
                        }
                    ]
                }
            ]
        },
    }
)


# ------------------------------------------------------------------ registry and availability


def test_registered():
    assert provider_class("cf-whisper-large-v3-turbo") is CfWhisperLargeV3Turbo
    assert provider_class("cf-whisper") is CfWhisper
    assert provider_class("cf-whisper-tiny-en") is CfWhisperTinyEn
    assert provider_class("cf-deepgram-nova-3") is CfDeepgramNova3


def test_model_ids_from_pricing():
    assert CfWhisperLargeV3Turbo().model_id == "@cf/openai/whisper-large-v3-turbo"
    assert CfWhisper().model_id == "@cf/openai/whisper"
    assert CfWhisperTinyEn().model_id == "@cf/openai/whisper-tiny-en"
    assert CfDeepgramNova3().model_id == "@cf/deepgram/nova-3"


@pytest.mark.parametrize("missing", ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"])
def test_availability_without_keys(monkeypatch, missing):
    monkeypatch.delenv(missing)
    ok, msg = CfWhisperLargeV3Turbo().availability()
    assert not ok and missing in msg and "cf-whisper-large-v3-turbo" in msg


def test_availability_with_keys():
    assert CfDeepgramNova3().availability() == (True, "ok")


# ------------------------------------------------------------------ whisper-large-v3-turbo


async def test_turbo_request_and_result(wav):
    seen: list[httpx.Request] = []
    p, _ = make(CfWhisperLargeV3Turbo, recorder(seen, TURBO))
    r = await p.transcribe(wav, "ja")
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == RUN + "@cf/openai/whisper-large-v3-turbo"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body["language"] == "ja" and body["task"] == "transcribe"
    assert base64.b64decode(body["audio"]) == wav.read_bytes()
    assert r.text == "こんにちは世界"
    assert [w.text for w in r.words] == ["こんにちは", "世界"]
    assert (r.words[1].start_ms, r.words[1].end_ms) == (500, 1000)
    assert r.audio_seconds == pytest.approx(3.0)
    # no usage reported: 46.63 neurons per minute at 0.011 per 1,000
    assert r.cost_usd == pytest.approx(0.00051293 * 3 / 60)
    assert r.model_id == "@cf/openai/whisper-large-v3-turbo"
    assert r.provider_version is None


async def test_cost_from_reported_neurons(wav):
    body = {**TURBO, "usage": {"neurons": 2.5}}
    p, _ = make(CfWhisperLargeV3Turbo, recorder([], body))
    r = await p.transcribe(wav, "ja")
    assert r.cost_usd == pytest.approx(2.5 * 0.011 / 1000)


async def test_turbo_long_audio_is_chunked(tmp_path):
    long = tmp_path / "long.wav"
    soundfile.write(str(long), np.zeros(16000 * 5, dtype="float32"), 16000)  # 5 s
    seen: list[httpx.Request] = []
    p, _ = make(CfWhisperLargeV3Turbo, recorder(seen, TURBO), chunk_seconds=2)
    r = await p.transcribe(long, "ja")
    assert len(seen) == 3  # 2 s + 2 s + 1 s
    durations = [
        soundfile.info(io.BytesIO(base64.b64decode(json.loads(s.content)["audio"]))).duration for s in seen
    ]
    assert durations == pytest.approx([2.0, 2.0, 1.0])
    assert r.text == "こんにちは世界" * 3
    assert [w.start_ms for w in r.words] == [0, 500, 2000, 2500, 4000, 4500]
    assert r.audio_seconds == pytest.approx(5.0)
    assert isinstance(r.raw_response, list) and len(r.raw_response) == 3


async def test_oversized_request_fails_clearly(wav, monkeypatch):
    monkeypatch.setattr(CfWhisperLargeV3Turbo, "max_request_bytes", 100)
    p, _ = make(CfWhisperLargeV3Turbo, recorder([], TURBO))
    with pytest.raises(ProviderError, match="exceeds 100"):
        await p.transcribe(wav, "ja")


async def test_retry_then_success(wav):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"success": False})
        return httpx.Response(200, json=TURBO)

    p, policy = make(CfWhisperLargeV3Turbo, handler)
    r = await p.transcribe(wav, "ja")
    assert r.text == "こんにちは世界" and state["n"] == 2
    assert policy.delays == [2.0]


async def test_fails_after_retries(wav):
    p, policy = make(
        CfDeepgramNova3, lambda request: httpx.Response(503, json={"success": False}), attempts=3
    )
    with pytest.raises(ProviderError, match="after 3 attempts"):
        await p.transcribe(wav, "hi")
    assert len(policy.delays) == 2


async def test_4xx_is_not_retried(wav):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "auth"}]})

    p, _ = make(CfWhisper, handler)
    with pytest.raises(ProviderError, match="403"):
        await p.transcribe(wav, "es")
    assert calls["n"] == 1


async def test_success_false_envelope_raises(wav):
    body = {"result": None, "success": False, "errors": [{"code": 5006, "message": "bad input"}]}
    p, _ = make(CfWhisperLargeV3Turbo, recorder([], body))
    with pytest.raises(ProviderError, match="bad input"):
        await p.transcribe(wav, "ja")


# ------------------------------------------------------------------ whisper and whisper-tiny-en


async def test_whisper_request_and_result(wav):
    seen: list[httpx.Request] = []
    p, _ = make(CfWhisper, recorder(seen, WHISPER))
    r = await p.transcribe(wav, "es")
    req = seen[0]
    assert str(req.url) == RUN + "@cf/openai/whisper"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.content == wav.read_bytes()  # raw bytes; the model has no language parameter
    assert not req.url.params
    assert r.text == "hola mundo"
    assert [(w.text, w.start_ms, w.end_ms) for w in r.words] == [("hola", 100, 400), ("mundo", 500, 900)]
    assert r.cost_usd == pytest.approx(0.00045254 * 3 / 60)


async def test_tiny_en_skips_other_languages(wav):
    seen: list[httpx.Request] = []
    p, _ = make(CfWhisperTinyEn, recorder(seen, WHISPER))
    for lang in ("ja", "ko", "es", "hi", "fr"):
        with pytest.raises(ProviderSkip, match="does not support"):
            await p.transcribe(wav, lang)
    assert seen == []
    r = await p.transcribe(wav, "en")
    assert str(seen[0].url) == RUN + "@cf/openai/whisper-tiny-en"
    assert r.text == "hola mundo"


async def test_unknown_language_skipped(wav):
    p, _ = make(CfWhisperLargeV3Turbo, recorder([], TURBO))
    with pytest.raises(ProviderSkip):
        await p.transcribe(wav, "de")


# ------------------------------------------------------------------ deepgram nova-3


async def test_nova3_request_and_result(wav):
    seen: list[httpx.Request] = []
    p, _ = make(CfDeepgramNova3, recorder(seen, NOVA))
    r = await p.transcribe(wav, "hi")
    req = seen[0]
    assert req.url.path == f"/client/v4/accounts/{ACCOUNT}/ai/run/@cf/deepgram/nova-3"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["content-type"] == "audio/wav"
    assert req.content == wav.read_bytes()
    assert dict(req.url.params) == {
        "language": "hi",
        "smart_format": "true",
        "punctuate": "true",
        "mip_opt_out": "true",
    }
    assert r.text == "नमस्ते दुनिया"
    assert [w.text for w in r.words] == ["नमस्ते", "दुनिया।"]
    assert r.words[0].start_ms == 200
    assert r.cost_usd == pytest.approx(0.0052 * 3 / 60)
    assert r.provider_version == "general-nova-3:2026-09-01.0"


async def test_nova3_bad_shape_raises(wav):
    p, _ = make(CfDeepgramNova3, recorder([], envelope({"results": {}})))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        await p.transcribe(wav, "en")


def test_settings_enter_cache_key():
    assert CfWhisperLargeV3Turbo().settings()["chunk_seconds"] == 240.0
    assert "chunk_seconds" not in CfDeepgramNova3().settings()
    assert CfWhisperLargeV3Turbo().settings_hash() != CfWhisperLargeV3Turbo(chunk_seconds=60).settings_hash()
