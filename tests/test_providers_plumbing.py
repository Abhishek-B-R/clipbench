"""Result.retries / Result.http_status plumbing and ProviderError attributes. Mocked transports only."""

from __future__ import annotations

import httpx
import numpy as np
import pytest
import soundfile

from clipbench.models import Result
from clipbench.providers.base import (
    Provider,
    ProviderError,
    call_stats,
    normalize_language_code,
)
from clipbench.providers.groq import GroqWhisper
from clipbench.providers.http import no_sleep_policy, retry_async


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "clip.wav"
    soundfile.write(str(p), np.zeros(16000, dtype="float32"), 16000)
    return p


def groq(handler, attempts=3):
    return GroqWhisper(transport=httpx.MockTransport(handler), retry=no_sleep_policy(attempts))


def ok_body():
    return {"text": "hola", "words": [], "language": "es"}


async def test_success_without_retries_has_status_200(wav):
    r = await groq(lambda req: httpx.Response(200, json=ok_body())).transcribe(wav, "es")
    assert r.retries == 0
    assert r.http_status == 200


async def test_retries_counted_on_result(wav):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=ok_body())

    r = await groq(handler, attempts=5).transcribe(wav, "es")
    assert r.retries == 2
    assert r.http_status == 200


async def test_network_error_retry_counted(wav):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json=ok_body())

    r = await groq(handler).transcribe(wav, "es")
    assert r.retries == 1 and r.http_status == 200


async def test_error_carries_status_and_retries_after_exhaustion(wav):
    p = groq(lambda req: httpx.Response(429, json={"error": "slow down"}), attempts=3)
    with pytest.raises(ProviderError) as info:
        await p.transcribe(wav, "es")
    assert info.value.http_status == 429
    assert info.value.retries == 2


async def test_non_retryable_error_carries_status(wav):
    p = groq(lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(ProviderError) as info:
        await p.transcribe(wav, "es")
    assert info.value.http_status == 401
    assert info.value.retries == 0


async def test_network_failure_has_no_status(wav):
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(ProviderError) as info:
        await groq(handler, attempts=2).transcribe(wav, "es")
    assert info.value.http_status is None
    assert info.value.retries == 1


async def test_parse_error_gets_stats_attached(wav):
    """A malformed 2xx body is a ProviderError (not a bare KeyError) and carries the call's stats."""
    p = groq(lambda req: httpx.Response(200, json={"words": [{"word": "x"}]}))  # no start/end
    with pytest.raises(ProviderError, match="unexpected response shape") as info:
        await p.transcribe(wav, "es")
    assert info.value.http_status == 200


async def test_non_provider_error_gets_stats_attached(wav):
    """Any other exception raised inside transcribe() after a request still carries the call's stats."""

    class Broken(GroqWhisper):
        async def transcribe(self, audio_path, language, *, clip=None):
            await self.http.request("POST", "/audio/transcriptions", data={"model": "m"})
            raise KeyError("boom")

    p = Broken(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})), retry=no_sleep_policy())
    with pytest.raises(KeyError) as info:
        await p.transcribe(wav, "es")
    assert info.value.http_status == 200  # type: ignore[attr-defined]


async def test_stats_are_per_call(wav):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, json={})
        return httpx.Response(200, json=ok_body())

    p = groq(handler)
    first = await p.transcribe(wav, "es")
    second = await p.transcribe(wav, "es")
    assert (first.retries, second.retries) == (1, 0)
    assert call_stats() is None


async def test_retry_async_counts_and_reads_boto_status():
    class Throttled(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 400}, "Error": {"Code": "ThrottlingException"}}

    state = {"n": 0}

    async def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise Throttled()
        return "ok"

    class Dummy(Provider):
        id = "dummy"

        async def transcribe(self, audio_path, language, *, clip=None):
            out = await retry_async(fn, no_sleep_policy(5), label="x", is_retryable=lambda e: True)
            return Result(text=out, latency_ms=0, audio_seconds=0, cost_usd=0, model_id="m")

    r = await Dummy("m").transcribe(None, "en")
    assert r.retries == 2 and r.http_status == 400


async def test_unsupported_options_recorded():
    class Plain(Provider):
        id = "plain"
        supported_options = frozenset({"diarize"})

        async def transcribe(self, audio_path, language, *, clip=None):
            return Result(text="", latency_ms=0, audio_seconds=0, cost_usd=0, model_id="m", raw_response=[1])

    p = Plain("m", diarize=True, detect_language=True, keyterms=["Acme"])
    r = await p.transcribe(None, "en")
    assert r.raw_response == {"response": [1], "clipbench_unsupported": ["detect_language", "keyterms"]}
    clean = await Plain("m").transcribe(None, "en")
    assert clean.raw_response == [1]


def test_normalize_language_code():
    assert normalize_language_code("ja-JP") == "ja"
    assert normalize_language_code("jpn") == "ja"
    assert normalize_language_code("Korean") == "ko"
    assert normalize_language_code("es_419") == "es"
    assert normalize_language_code("") is None
    assert normalize_language_code(None) is None
