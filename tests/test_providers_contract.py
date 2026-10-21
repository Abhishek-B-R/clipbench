"""The same contract checked against every batch adapter, with httpx.MockTransport (or fake boto3 clients for
AWS). No network, no keys, no real sleeps.

Per adapter: auth placement, a valid result, a valid empty (silent) result, malformed 2xx bodies, retries on
429 (Retry-After honoured) and 5xx, per request timeouts, the job timeout, and billing.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np
import pytest
import soundfile

from clipbench.providers.assemblyai import AssemblyAI
from clipbench.providers.base import ProviderError
from clipbench.providers.cloudflare import CfDeepgramNova3, CfWhisper, CfWhisperLargeV3Turbo, CfWhisperTinyEn
from clipbench.providers.deepgram import Deepgram
from clipbench.providers.elevenlabs import ElevenLabsScribe
from clipbench.providers.gemini import GeminiFlash
from clipbench.providers.groq import GroqWhisper
from clipbench.providers.http import no_sleep_policy

KEY = "test-key"
ENV = ["GROQ_API_KEY", "DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY",
       "CLOUDFLARE_API_TOKEN"]  # fmt: skip


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for k in ENV:
        monkeypatch.setenv(k, KEY)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.delenv("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", raising=False)


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "clip.wav"
    soundfile.write(str(p), np.zeros(16000 * 3, dtype="float32"), 16000)  # 3 s
    return p


def env(result: dict) -> dict:
    return {"result": result, "success": True, "errors": [], "messages": []}


def dg(transcript: str, words: list[dict], duration: float | None = 3.0) -> dict:
    body: dict[str, Any] = {"results": {"channels": [{"alternatives": [{"transcript": transcript, "words": words}]}]}}
    if duration is not None:
        body["metadata"] = {"duration": duration}
    return body


DG_WORDS = [{"word": "hola", "punctuated_word": "Hola", "start": 0.1, "end": 0.4}]
AAI_DONE = {"id": "t1", "status": "completed", "text": "hola", "words": [{"text": "hola", "start": 100, "end": 400}],
            "audio_duration": 3, "speech_model_used": "universal-3-5-pro"}  # fmt: skip
GEM_OK = {"candidates": [{"content": {"parts": [{"text": "hola"}]}}], "modelVersion": "gemini-3.8-flash-001"}


@dataclass
class Spec:
    cls: type
    language: str
    ok: Any  # the main response body when everything works
    empty: Any  # a valid reply for silent audio
    missing: Any  # a JSON object without the documented fields
    auth: tuple[str, str]
    text: str = "hola"
    kwargs: dict = field(default_factory=dict)


SPECS = {
    "groq-whisper": Spec(GroqWhisper, "es", {"text": "hola", "words": [{"word": "hola", "start": 0.1, "end": 0.4}]},
                         {"text": "", "words": []}, {}, ("authorization", f"Bearer {KEY}")),
    "deepgram": Spec(Deepgram, "es", dg("hola", DG_WORDS), dg("", []), {}, ("authorization", f"Token {KEY}")),
    "assemblyai": Spec(AssemblyAI, "es", AAI_DONE,
                       {"id": "t1", "status": "completed", "text": "", "words": [], "audio_duration": 3},
                       {}, ("authorization", KEY)),
    "elevenlabs-scribe": Spec(ElevenLabsScribe, "es",
                              {"text": "hola", "words": [{"text": "hola", "type": "word", "start": 0.1, "end": 0.4}]},
                              {"text": "", "words": []}, {}, ("xi-api-key", KEY)),
    "gemini-flash": Spec(GeminiFlash, "es", GEM_OK, {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]},
                         {}, ("x-goog-api-key", KEY)),
    "cf-whisper-large-v3-turbo": Spec(CfWhisperLargeV3Turbo, "es",
                                      env({"text": "hola", "segments": [{"words": [{"word": "hola", "start": 0.1,
                                                                                     "end": 0.4}]}]}),
                                      env({"text": ""}), {}, ("authorization", f"Bearer {KEY}")),
    "cf-whisper": Spec(CfWhisper, "es", env({"text": "hola", "words": [{"word": "hola", "start": 0.1, "end": 0.4}]}),
                       env({"text": ""}), {}, ("authorization", f"Bearer {KEY}")),
    "cf-whisper-tiny-en": Spec(CfWhisperTinyEn, "en",
                               env({"text": "hello", "words": [{"word": "hello", "start": 0.1, "end": 0.4}]}),
                               env({"text": ""}), {}, ("authorization", f"Bearer {KEY}"), text="hello"),
    "cf-deepgram-nova-3": Spec(CfDeepgramNova3, "es", env(dg("hola", DG_WORDS)), env(dg("", [])), env({}),
                               ("authorization", f"Bearer {KEY}")),
}  # fmt: skip
IDS = list(SPECS)


def is_main(request: httpx.Request, pid: str) -> bool:
    """The request whose response carries the transcript (AssemblyAI: the polling GET)."""
    if pid == "assemblyai":
        return request.method == "GET"
    return True


def handler_for(pid: str, main: Any, seen: list[httpx.Request] | None = None):
    """Route AssemblyAI's upload / submit / delete; every other adapter makes one request per clip."""

    def h(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if pid == "assemblyai":
            if request.url.path == "/v2/upload":
                return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/a"})
            if request.url.path == "/v2/transcript" and request.method == "POST":
                return httpx.Response(200, json={"id": "t1", "status": "queued"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"id": "t1"})
        if isinstance(main, httpx.Response):
            return main
        return httpx.Response(200, json=main)

    return h


def first_fails(inner, response: httpx.Response | Exception, times: int = 1):
    """The first `times` requests get `response` (or raise it), then `inner` answers."""
    state = {"n": 0}

    def h(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] <= times:
            if isinstance(response, Exception):
                raise response
            return response
        return inner(request)

    h.state = state  # type: ignore[attr-defined]
    return h


def make(pid: str, handler, attempts: int = 3, **kw):
    spec = SPECS[pid]
    policy = no_sleep_policy(attempts)
    p = spec.cls(transport=httpx.MockTransport(handler), retry=policy, **{**spec.kwargs, **kw})
    return p, policy


# ---------------------------------------------------------------- request


@pytest.mark.parametrize("pid", IDS)
async def test_auth_goes_in_a_header_never_in_the_url(pid, wav):
    seen: list[httpx.Request] = []
    p, _ = make(pid, handler_for(pid, SPECS[pid].ok, seen))
    await p.transcribe(wav, SPECS[pid].language)
    name, value = SPECS[pid].auth
    assert seen
    for req in seen:
        assert req.headers[name] == value
        assert KEY not in str(req.url)


@pytest.mark.parametrize("pid", ["deepgram", "cf-deepgram-nova-3"])
async def test_deepgram_opt_out_on_every_request(pid, wav):
    seen: list[httpx.Request] = []
    p, _ = make(pid, handler_for(pid, SPECS[pid].ok, seen))
    await p.transcribe(wav, "es")
    assert all(r.url.params.get("mip_opt_out") == "true" for r in seen)


async def test_gemini_model_in_path_and_language_in_prompt(wav):
    seen: list[httpx.Request] = []
    p, _ = make("gemini-flash", handler_for("gemini-flash", GEM_OK, seen))
    await p.transcribe(wav, "ko")
    assert seen[0].url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert "key" not in seen[0].url.params
    assert "Korean" in json.loads(seen[0].content)["contents"][0]["parts"][0]["text"]


# ---------------------------------------------------------------- response parsing


@pytest.mark.parametrize("pid", IDS)
async def test_valid_result(pid, wav):
    p, policy = make(pid, handler_for(pid, SPECS[pid].ok))
    r = await p.transcribe(wav, SPECS[pid].language)
    assert r.text == SPECS[pid].text
    assert r.audio_seconds == pytest.approx(3.0)
    assert r.cost_usd > 0 and r.latency_ms >= 0
    assert (r.http_status, r.retries) == (200, 0)
    assert r.model_id == p.model_id


@pytest.mark.parametrize("pid", IDS)
async def test_valid_empty_transcript_is_an_empty_result_and_still_billed(pid, wav):
    p, _ = make(pid, handler_for(pid, SPECS[pid].empty))
    r = await p.transcribe(wav, SPECS[pid].language)
    assert r.text == "" and r.words is None
    assert r.cost_usd > 0


@pytest.mark.parametrize("pid", IDS)
@pytest.mark.parametrize("kind", ["non_json", "json_list", "missing_fields"])
async def test_malformed_2xx_body_is_a_provider_error_and_not_retried(pid, kind, wav):
    spec = SPECS[pid]
    seen: list[httpx.Request] = []
    if kind == "non_json":
        main: Any = httpx.Response(200, content=b"<html>gateway</html>", headers={"content-type": "text/html"})
    elif kind == "json_list":
        main = [1, 2]
    else:
        main = spec.missing
    handler = handler_for(pid, main, seen)
    if pid == "assemblyai" and kind == "missing_fields":
        # An upload reply without upload_url (a poll reply without status keeps polling until the job timeout).
        handler = handler_for(pid, AAI_DONE, seen)
        handler = first_fails(handler, httpx.Response(200, json={}))
    p, _ = make(pid, handler)
    with pytest.raises(ProviderError) as info:
        await p.transcribe(wav, spec.language)
    assert info.value.http_status == 200
    assert info.value.retries == 0  # a 2xx is never retried (AssemblyAI's poll wait is not a retry)
    if pid != "assemblyai":
        assert len(seen) == 1


# ---------------------------------------------------------------- retries


@pytest.mark.parametrize("pid", IDS)
async def test_429_with_retry_after_then_success(pid, wav):
    p, policy = make(pid, first_fails(handler_for(pid, SPECS[pid].ok),
                                      httpx.Response(429, headers={"retry-after": "4"}, json={"error": "slow"})))  # fmt: skip
    r = await p.transcribe(wav, SPECS[pid].language)
    assert r.text == SPECS[pid].text
    assert policy.delays[0] == 4.0  # Retry-After honoured (later delays are AssemblyAI polling)
    assert r.retries == 1 and r.http_status == 200


@pytest.mark.parametrize("pid", IDS)
async def test_5xx_then_success_uses_backoff(pid, wav):
    p, policy = make(pid, first_fails(handler_for(pid, SPECS[pid].ok), httpx.Response(503, json={})))
    r = await p.transcribe(wav, SPECS[pid].language)
    assert r.text == SPECS[pid].text
    assert policy.delays[0] == 1.0  # base delay, no jitter in tests
    assert r.retries == 1


@pytest.mark.parametrize("pid", IDS)
async def test_5xx_exhausted_raises_with_status_and_retries(pid, wav):
    p, policy = make(pid, lambda r: httpx.Response(500, json={"error": "down"}), attempts=3)
    with pytest.raises(ProviderError, match="after 3 attempts") as info:
        await p.transcribe(wav, SPECS[pid].language)
    assert info.value.http_status == 500 and info.value.retries == 2
    assert len(policy.delays) == 2


@pytest.mark.parametrize("pid", IDS)
async def test_4xx_fails_at_once(pid, wav):
    seen: list[httpx.Request] = []

    def h(request):
        seen.append(request)
        return httpx.Response(400, json={"error": "bad request"})

    p, policy = make(pid, h)
    with pytest.raises(ProviderError, match="400") as info:
        await p.transcribe(wav, SPECS[pid].language)
    assert len(seen) == 1 and policy.delays == [] and info.value.http_status == 400


# ---------------------------------------------------------------- timeouts


@pytest.mark.parametrize("pid", IDS)
async def test_request_timeout_is_retried_then_fails(pid, wav):
    def h(request):
        raise httpx.ReadTimeout("timed out", request=request)

    p, policy = make(pid, h, attempts=2)
    with pytest.raises(ProviderError, match="ReadTimeout") as info:
        await p.transcribe(wav, SPECS[pid].language)
    assert info.value.http_status is None and info.value.retries == 1


@pytest.mark.parametrize("pid", IDS)
async def test_request_timeout_then_success(pid, wav):
    inner = handler_for(pid, SPECS[pid].ok)

    def h(request):
        if not getattr(h, "fired", False):
            h.fired = True  # type: ignore[attr-defined]
            raise httpx.ConnectTimeout("connect timed out", request=request)
        return inner(request)

    p, _ = make(pid, h)
    r = await p.transcribe(wav, SPECS[pid].language)
    assert r.text == SPECS[pid].text and r.retries == 1 and r.http_status == 200


@pytest.mark.parametrize("pid", IDS)
async def test_client_timeout_is_timeout_s_and_job_timeout_covers_every_attempt(pid):
    p, policy = make(pid, handler_for(pid, SPECS[pid].ok), attempts=5)
    timeout = p.http.client.timeout
    assert timeout.read == p.timeout_s and timeout.connect == min(30.0, p.timeout_s)
    assert p.job_timeout_s() >= p.timeout_s * policy.max_attempts + policy.max_retry_after_s * 4


async def test_assemblyai_poll_timeout_raises_and_still_deletes(wav):
    seen: list[httpx.Request] = []
    p, _ = make("assemblyai", handler_for("assemblyai", {"id": "t1", "status": "processing"}, seen))
    p.poll_timeout_s = -1.0  # the deadline has passed by the first poll
    with pytest.raises(ProviderError, match="timed out while polling"):
        await p.transcribe(wav, "es")
    assert seen[-1].method == "DELETE" and seen[-1].url.path == "/v2/transcript/t1"
    assert AssemblyAI().job_timeout_s() >= AssemblyAI.poll_timeout_s + AssemblyAI.timeout_s


# ---------------------------------------------------------------- billing


async def test_deepgram_cost_uses_billed_duration_when_reported(wav):
    p, _ = make("deepgram", handler_for("deepgram", dg("hola", DG_WORDS, duration=5.0)))
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.0043 * 5 / 60)
    p, _ = make("deepgram", handler_for("deepgram", dg("hola", DG_WORDS, duration=None)))
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.0043 * 3 / 60)  # WAV length


async def test_assemblyai_cost_uses_audio_duration_and_model_used(wav):
    p, _ = make("assemblyai", handler_for("assemblyai", {**AAI_DONE, "audio_duration": 4.0}))
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.0035 * 4 / 60)
    body = {**AAI_DONE, "speech_model_used": "universal-2"}
    body.pop("audio_duration")
    p, _ = make("assemblyai", handler_for("assemblyai", body))
    assert (await p.transcribe(wav, "ko")).cost_usd == pytest.approx(0.0025 * 3 / 60)


async def test_gemini_cost_without_usage_is_the_estimate(wav):
    p, _ = make("gemini-flash", handler_for("gemini-flash", GEM_OK))
    r = await p.transcribe(wav, "es")
    assert r.cost_usd == pytest.approx(0.0034 * 3 / 60) == pytest.approx(p.estimate_cost(3.0))


async def test_groq_minimum_billing(wav, tmp_path):
    p, _ = make("groq-whisper", handler_for("groq-whisper", SPECS["groq-whisper"].ok))
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.00185 * 10 / 60)  # 3 s billed as 10 s
    long = tmp_path / "long.wav"
    soundfile.write(str(long), np.zeros(16000 * 12, dtype="float32"), 16000)
    assert (await p.transcribe(long, "es")).cost_usd == pytest.approx(0.00185 * 12 / 60)


async def test_elevenlabs_keyterm_surcharge_and_minimum_in_the_result(wav):
    seen: list[httpx.Request] = []
    p, _ = make("elevenlabs-scribe", handler_for("elevenlabs-scribe", SPECS["elevenlabs-scribe"].ok, seen),
                keyterms=["Acme"])  # fmt: skip
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.003667 * 3 / 60 * 1.2)
    many = [f"term{i}" for i in range(101)]
    p, _ = make("elevenlabs-scribe", handler_for("elevenlabs-scribe", SPECS["elevenlabs-scribe"].ok), keyterms=many)
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.003667 * 20 / 60 * 1.2)


async def test_elevenlabs_without_keyterms_has_no_surcharge(wav):
    p, _ = make("elevenlabs-scribe", handler_for("elevenlabs-scribe", SPECS["elevenlabs-scribe"].ok))
    assert (await p.transcribe(wav, "es")).cost_usd == pytest.approx(0.003667 * 3 / 60)


async def test_cloudflare_neurons_summed_over_chunks_else_estimate(wav):
    def with_neurons(n):
        return {**SPECS["cf-whisper-large-v3-turbo"].ok, "usage": {"neurons": n}}

    p, _ = make("cf-whisper-large-v3-turbo", handler_for("cf-whisper-large-v3-turbo", with_neurons(2.0)),
                chunk_seconds=1.0)  # fmt: skip
    r = await p.transcribe(wav, "es")  # 3 chunks of 1 s
    assert r.cost_usd == pytest.approx(3 * 2.0 * 0.011 / 1000)
    bodies = iter([with_neurons(2.0), SPECS["cf-whisper-large-v3-turbo"].ok, with_neurons(2.0)])
    p, _ = make("cf-whisper-large-v3-turbo", lambda req: httpx.Response(200, json=next(bodies)), chunk_seconds=1.0)
    r = await p.transcribe(wav, "es")  # one chunk reported nothing: fall back to the estimate
    assert r.cost_usd == pytest.approx(p.estimate_cost(3.0))


# ---------------------------------------------------------------- AWS (fake boto3 clients)


class FakeS3:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        return {}

    def get_object(self, **kw):
        self.calls.append(("get_object", kw))
        return {"Body": io.BytesIO(self.payload)}

    def delete_object(self, **kw):
        self.calls.append(("delete_object", kw))
        return {}


class FakeTranscribe:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = list(statuses)
        self.calls: list[tuple[str, dict]] = []

    def start_transcription_job(self, **kw):
        self.calls.append(("start_transcription_job", kw))
        return {}

    def get_transcription_job(self, **kw):
        self.calls.append(("get_transcription_job", kw))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"TranscriptionJob": {"TranscriptionJobName": kw["TranscriptionJobName"],
                                     "TranscriptionJobStatus": status}}  # fmt: skip

    def delete_transcription_job(self, **kw):
        self.calls.append(("delete_transcription_job", kw))
        return {}


@pytest.fixture
def aws_env(monkeypatch):
    for k, v in (("AWS_ACCESS_KEY_ID", "AKIDTEST"), ("AWS_SECRET_ACCESS_KEY", "s"), ("AWS_REGION", "us-east-1"),
                 ("CLIPBENCH_AWS_BUCKET", "clipbench-test")):  # fmt: skip
        monkeypatch.setenv(k, v)


def aws(payload: bytes, statuses: list[str]):
    from clipbench.providers.aws_transcribe import AwsTranscribe

    s3, tx = FakeS3(payload), FakeTranscribe(statuses)
    return AwsTranscribe(retry=no_sleep_policy(2), s3_client=s3, transcribe_client=tx), s3, tx


def names(calls):
    return [n for n, _ in calls]


AWS_OK = {"results": {"transcripts": [{"transcript": "hola"}], "items": [
    {"type": "pronunciation", "start_time": "0.1", "end_time": "0.4", "alternatives": [{"content": "hola"}]}]}}  # fmt: skip


async def test_aws_request_payload_and_result(wav, aws_env):
    p, s3, tx = aws(json.dumps(AWS_OK).encode(), ["IN_PROGRESS", "COMPLETED"])
    r = await p.transcribe(wav, "es")
    start = dict(tx.calls)["start_transcription_job"]
    assert start["LanguageCode"] == "es-US" and start["MediaFormat"] == "wav"
    assert start["OutputBucketName"] == "clipbench-test" and start["Media"]["MediaFileUri"].startswith("s3://clipbench-test/")
    assert r.text == "hola" and r.words[0].start_ms == 100
    assert r.cost_usd == pytest.approx(0.024 * 3 / 60)
    assert names(s3.calls).count("delete_object") == 2 and "delete_transcription_job" in names(tx.calls)


async def test_aws_empty_transcript(wav, aws_env):
    p, _, _ = aws(json.dumps({"results": {"transcripts": [{"transcript": ""}], "items": []}}).encode(), ["COMPLETED"])
    r = await p.transcribe(wav, "es")
    assert r.text == "" and r.words is None and r.cost_usd > 0


async def test_aws_malformed_transcript_is_a_provider_error_and_cleans_up(wav, aws_env):
    p, s3, tx = aws(b"not json", ["COMPLETED"])
    with pytest.raises(ProviderError, match="unexpected response shape"):
        await p.transcribe(wav, "es")
    assert names(s3.calls).count("delete_object") == 2 and "delete_transcription_job" in names(tx.calls)


async def test_aws_job_timeout_raises_and_cleans_up(wav, aws_env):
    p, s3, tx = aws(b"{}", ["IN_PROGRESS"])
    p.timeout_s = -1.0  # the polling deadline has passed by the first status check
    with pytest.raises(ProviderError, match="timed out"):
        await p.transcribe(wav, "es")
    assert "get_object" not in names(s3.calls)
    assert names(s3.calls).count("delete_object") == 2 and "delete_transcription_job" in names(tx.calls)
    from clipbench.providers.aws_transcribe import AwsTranscribe

    assert AwsTranscribe(s3_client=object(), transcribe_client=object()).job_timeout_s() > AwsTranscribe.timeout_s


async def test_quota_exhaustion_is_not_a_rate_limit_and_stops_the_provider(tmp_settings):
    import httpx

    from clipbench.providers.base import ProviderQuotaExhausted
    from clipbench.providers.http import HttpClient, RetryPolicy

    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"errors": [{"message": "you have used up your daily free allocation of 10,000 neurons"}]})

    sleeps: list[float] = []

    async def no_sleep(s):
        sleeps.append(s)

    client = HttpClient("cf", httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
                        RetryPolicy(sleep=no_sleep))
    with pytest.raises(ProviderQuotaExhausted):
        await client.request("POST", "/run")
    assert calls == RetryPolicy().max_attempts  # retried like any 429 first, then classified as quota

    def billing(request):
        return httpx.Response(402, json={"error": "payment required"})

    client = HttpClient("x", httpx.AsyncClient(transport=httpx.MockTransport(billing), base_url="https://x"),
                        RetryPolicy(sleep=no_sleep))
    with pytest.raises(ProviderQuotaExhausted):
        await client.request("POST", "/run")


async def test_rate_limit_message_is_not_mistaken_for_quota():
    import httpx

    from clipbench.providers.base import ProviderError, ProviderQuotaExhausted
    from clipbench.providers.http import HttpClient, RetryPolicy

    def handler(request):
        return httpx.Response(429, json={"error": {"message": "Rate limit reached for model on requests per minute (RPM): Limit 20, Used 20"}})

    async def no_sleep(s):
        pass

    client = HttpClient("groq", httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
                        RetryPolicy(sleep=no_sleep))
    with pytest.raises(ProviderError) as info:
        await client.request("POST", "/t")
    assert not isinstance(info.value, ProviderQuotaExhausted)


async def test_requests_per_minute_paces_requests(monkeypatch):
    import httpx

    from clipbench.providers import http as http_mod
    from clipbench.providers.http import HttpClient, RetryPolicy

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(http_mod, "_pace_sleep", fake_sleep)

    client = HttpClient(
        "groq",
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), base_url="https://x"),
        RetryPolicy(),
        requests_per_minute=60,
    )
    for _ in range(3):
        await client.request("POST", "/t")
    # The fake sleep does not advance the loop clock, so each request queues one interval (1 s) later.
    assert [round(x) for x in sleeps] == [1, 2]


async def test_runner_stops_a_provider_on_quota_and_resume_retries_it(tmp_settings):
    from clipbench.providers.base import ProviderQuotaExhausted
    from clipbench.providers.fake import FakeProvider
    from clipbench.runner import QUOTA_ERROR, run
    from tests.fixtures.make_clips import make_clips

    clips = make_clips(tmp_settings.data_dir / "clips")

    class Broke(FakeProvider):
        id = "fake-broke"
        n = 0

        async def transcribe(self, audio_path, language, *, clip=None):
            Broke.n += 1
            raise ProviderQuotaExhausted("out of credit", http_status=429)

    p = Broke()
    p.concurrency = 1
    res = await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-quota")
    rows = res.rows
    assert Broke.n == 1  # stopped after the first quota error, not one call per clip
    assert set(rows["error"]) == {QUOTA_ERROR} and (rows["status"] == "skipped").all()

    class Fixed(FakeProvider):
        id = "fake-broke"

    res2 = await run([Fixed()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-quota")
    assert (res2.rows["status"] == "ok").all()  # quota skips are retried on resume


def test_groq_is_paced_below_its_documented_limit():
    from clipbench.providers.groq import GroqWhisper

    assert GroqWhisper.requests_per_minute is not None and GroqWhisper.requests_per_minute < 20
