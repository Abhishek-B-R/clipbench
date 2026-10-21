"""The streaming contract checked against every realtime adapter with the in-memory server. No socket is opened.

Per adapter: auth placement and the opt-out query flags, junk and empty server messages, provider errors, the
ready and final timeouts, billing. Handshake failures (a 429 or 5xx on the upgrade) are not retried: the
runner records an error row and counts the estimate as spent, since audio may have been billed.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import numpy as np
import pytest
import soundfile
from rich.console import Console

from clipbench.models import Clip, Reference
from clipbench.streaming import elevenlabs as elevenlabs_mod
from clipbench.streaming import gemini as gemini_mod
from clipbench.streaming.assemblyai import AssemblyAIStream
from clipbench.streaming.base import FakeClock, StreamError
from clipbench.streaming.cloudflare import CfNova3Stream
from clipbench.streaming.deepgram import DeepgramStream
from clipbench.streaming.elevenlabs import ElevenLabsStream
from clipbench.streaming.fakeserver import memory_connect
from clipbench.streaming.gemini import GeminiStream
from clipbench.streaming.metrics import final_text
from clipbench.streaming.pacer import Pacer
from clipbench.streaming.session import stream_audio
from tests.test_streaming_adapters import (
    PCM,
    assemblyai_server,
    deepgram_server,
    dg_results,
    elevenlabs_server,
    gemini_server,
    turn,
)

KEY = "test-key"
JUNK = ["not json", b"\xff\xfe\x00", json.dumps([1, 2]), json.dumps({"type": "SomethingNew"}), "null"]


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for k in ("DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY",
              "CLOUDFLARE_API_TOKEN"):  # fmt: skip
        monkeypatch.setenv(k, KEY)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.delenv("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", raising=False)


async def run(provider, handler, language="ja", quiet_s=None, final_timeout_s=2.0):
    clock = FakeClock()
    connect, server = memory_connect(lambda srv, url, headers: handler(srv, clock))
    t = await stream_audio(provider, PCM, language, pacer=Pacer(chunk_ms=100, clock=clock), connect=connect,
                           quiet_s=quiet_s, final_timeout_s=final_timeout_s)  # fmt: skip
    return t, server


def with_junk(server_fn):
    """Wrap a scripted server so it sends every JUNK message before its own first reply."""

    async def wrapped(srv, clock):
        class Proxy:
            sent_junk = False

            async def send(self, data):
                if not Proxy.sent_junk:
                    Proxy.sent_junk = True
                    for j in JUNK:
                        await srv.send(j)
                await srv.send(data)

            async def recv(self):
                return await srv.recv()

            async def close(self):
                await srv.close()

            @property
            def received(self):
                return srv.received

        await server_fn(Proxy(), clock)

    return wrapped


# (provider factory, server, language, quiet_s, expected final text, auth header, auth value)
CASES = {
    "deepgram-stream": (DeepgramStream, deepgram_server, "ja", None, "こんにちは。", "authorization", f"Token {KEY}"),
    "cf-nova-3-stream": (CfNova3Stream, deepgram_server, "ja", None, "こんにちは。", "authorization", f"Bearer {KEY}"),
    "assemblyai-stream": (AssemblyAIStream, assemblyai_server, "es", None, "Hola mundo.", "authorization", KEY),
    "elevenlabs-stream": (ElevenLabsStream, elevenlabs_server, "ko", 0.1, "안녕하세요", "xi-api-key", KEY),
    "gemini-stream": (GeminiStream, gemini_server, "es", 0.1, "Buenos días.", "x-goog-api-key", KEY),
}  # fmt: skip
IDS = list(CASES)


# ---------------------------------------------------------------- request


@pytest.mark.parametrize("pid", IDS)
async def test_auth_in_a_header_never_in_the_url(pid):
    cls, server, lang, quiet, _, header, value = CASES[pid]
    _, srv = await run(cls(), server, lang, quiet)
    conn = srv.connections[0]
    assert conn["headers"][header] == value
    assert KEY not in conn["url"] and "key=" not in conn["url"]


@pytest.mark.parametrize("pid", ["deepgram-stream", "cf-nova-3-stream"])
async def test_deepgram_shaped_query(pid):
    cls, server, lang, quiet, *_ = CASES[pid]
    _, srv = await run(cls(), server, lang, quiet)
    url = srv.connections[0]["url"]
    for part in ("mip_opt_out=true", "encoding=linear16", "sample_rate=16000", "channels=1", "language=ja"):
        assert part in url


async def test_elevenlabs_zero_retention_flag(monkeypatch):
    assert "enable_logging" not in ElevenLabsStream().url("ko")
    monkeypatch.setenv("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", "1")
    assert "enable_logging=false" in ElevenLabsStream().url("ko")


def test_language_codes_on_the_wire():
    assert "language_codes=ja" in AssemblyAIStream().url("ja-JP")
    assert "language_code=ja" in ElevenLabsStream().url("ja-JP")
    assert GeminiStream().setup_message("es")["setup"]["inputAudioTranscription"]["languageCodes"] == ["es-419"]
    assert GeminiStream().setup_message("hi-IN")["setup"]["inputAudioTranscription"]["languageCodes"] == ["hi-IN"]


# ---------------------------------------------------------------- server messages


@pytest.mark.parametrize("pid", IDS)
async def test_junk_and_unknown_messages_are_ignored(pid):
    # Elevenlabs and Gemini get the junk before their ready message (skipped by recv_json), the others mid stream.
    cls, server, lang, quiet, text, *_ = CASES[pid]
    t, srv = await run(cls(), with_junk(server), lang, quiet)
    assert not srv.errors
    assert final_text(t.events) == text


async def test_empty_finals_are_dropped():
    p = AssemblyAIStream()
    assert p.parse(json.loads(turn(0, [], True, "")), p.new_state()) == []
    e = ElevenLabsStream()
    state = e.new_state()
    assert e.parse({"message_type": "committed_transcript_with_timestamps", "text": " ", "words": []}, state) == []
    assert state["segment"] == 1  # the segment still advances
    g = GeminiStream()
    assert g.parse({"serverContent": {"inputTranscription": {"text": "  "}}}, g.new_state()) == []
    d = DeepgramStream()
    assert d.parse(json.loads(dg_results("", True)), d.new_state()) == []


def test_words_without_timings_are_dropped():
    d = DeepgramStream()
    msg = json.loads(dg_results("hola", True, [{"word": "hola"}]))
    assert d.parse(msg, d.new_state())[0].words is None
    a = AssemblyAIStream()
    out = a.parse(json.loads(turn(0, [{"text": "hola"}], True, "hola")), a.new_state())
    assert out[0].text == "hola" and out[0].words is None


@pytest.mark.parametrize(
    "provider, message",
    [
        (DeepgramStream, {"type": "Error", "description": "quota"}),
        (CfNova3Stream, {"type": "Error", "message": "quota"}),
        (AssemblyAIStream, {"type": "Error", "error": "quota"}),
        (AssemblyAIStream, {"error": "quota"}),
        (ElevenLabsStream, {"message_type": "quota_exceeded", "message": "quota"}),
        (ElevenLabsStream, {"message_type": "input_error", "error": "quota"}),
        (GeminiStream, {"error": {"code": 429, "message": "quota"}}),
    ],
)
def test_provider_error_messages_raise(provider, message):
    p = provider()
    with pytest.raises(StreamError, match="quota"):
        p.parse(message, p.new_state())


# ---------------------------------------------------------------- timeouts


@pytest.mark.parametrize("pid", ["elevenlabs-stream", "gemini-stream"])
async def test_ready_timeout_when_the_server_never_says_ready(pid, monkeypatch):
    monkeypatch.setattr(elevenlabs_mod, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(gemini_mod, "READY_TIMEOUT_S", 0.05)

    async def silent(srv, clock):
        while True:
            await srv.recv()

    cls = CASES[pid][0]
    with pytest.raises(TimeoutError):
        await run(cls(), silent, CASES[pid][2])


@pytest.mark.parametrize("pid", ["deepgram-stream", "cf-nova-3-stream", "assemblyai-stream"])
async def test_final_timeout_without_terminal_message(pid):
    """No Metadata / Termination: the transcript is kept with timed_out, billed from the audio or socket time."""

    async def no_terminal(srv, clock):
        while True:
            await srv.recv()

    cls = CASES[pid][0]
    p = cls()
    t, _ = await run(p, no_terminal, CASES[pid][2], final_timeout_s=0.1)
    assert t.timed_out and t.events == []
    assert "billed_audio_s" not in t.meta and "billed_session_s" not in t.meta
    if p.billing == "session":
        assert p.actual_cost(t) == pytest.approx(p.price_per_minute() * t.session_s / 60)
    else:
        assert p.actual_cost(t) == pytest.approx(p.price_per_minute() * t.audio_seconds / 60)


# ---------------------------------------------------------------- billing


@pytest.mark.parametrize(
    "pid, usd_per_minute, billing",
    [("deepgram-stream", 0.0077, "audio"), ("cf-nova-3-stream", 0.0092, "audio"),
     ("assemblyai-stream", 0.0075, "session"), ("elevenlabs-stream", 0.0065, "audio"),
     ("gemini-stream", 0.009, "audio")],
)  # fmt: skip
def test_estimate_per_billing_basis(pid, usd_per_minute, billing):
    p = CASES[pid][0]()
    assert (p.price_per_minute(), p.billing) == (usd_per_minute, billing)
    expected_s = 60 + 3 if billing == "session" else 60  # session: wall time plus session_overhead_s
    assert p.estimate_cost(60) == pytest.approx(usd_per_minute * expected_s / 60)
    if billing == "session":
        assert p.estimate_cost(60, speed=2.0) == pytest.approx(usd_per_minute * 33 / 60)


async def test_reported_billing_overrides_the_estimate():
    t, _ = await run(DeepgramStream(), deepgram_server, "ja")
    assert DeepgramStream().actual_cost(t) == pytest.approx(0.0077 * 0.3 / 60)  # Metadata.duration
    t, _ = await run(AssemblyAIStream(), assemblyai_server, "es")
    assert AssemblyAIStream().actual_cost(t) == pytest.approx(0.0075 * 1.2 / 60)  # session_duration_seconds
    t, _ = await run(GeminiStream(), gemini_server, "es", 0.1)
    assert GeminiStream().actual_cost(t) == pytest.approx(0.009 * t.audio_seconds / 60)  # usage not priced


# ---------------------------------------------------------------- runner: handshake failure, override


def clip(tmp_path) -> Clip:
    wav = tmp_path / "a.wav"
    soundfile.write(str(wav), np.zeros(4800, dtype="float32"), 16000)
    ref = Reference(video_id="a", language="es", tier="verified", text="Hola mundo.")
    return Clip(video_id="a", language="es", duration_s=0.3, audio_path=str(wav), audio_sha256="sha-a", reference=ref)


async def test_handshake_rejection_is_an_error_row_not_retried(tmp_settings, tmp_path):
    from clipbench.budget import load_spend
    from clipbench.streaming.runner import run_stream

    attempts = {"n": 0}

    @asynccontextmanager
    async def rejecting(url, headers):
        attempts["n"] += 1
        raise ConnectionError("server rejected WebSocket connection: HTTP 429")
        yield  # pragma: no cover

    p = DeepgramStream()
    res = await run_stream([p], "verified", clips=[clip(tmp_path)], settings=tmp_settings, connect=rejecting,
                           clock=FakeClock(), quiet_s=0.05, console=Console(record=True))  # fmt: skip
    row = res.rows.iloc[0]
    assert row["status"] == "error" and "HTTP 429" in row["error"]
    assert attempts["n"] == 1
    assert load_spend(tmp_settings.spend_path)["total_usd"] == pytest.approx(p.estimate_cost(0.3))


async def test_override_real_stream_run_is_recorded(tmp_settings, tmp_path):
    from clipbench.streaming.runner import run_stream

    connect, _ = memory_connect(lambda srv, url, headers: assemblyai_server(srv, FakeClock()))
    res = await run_stream([AssemblyAIStream()], "verified", clips=[clip(tmp_path)], settings=tmp_settings,
                           connect=connect, clock=FakeClock(), quiet_s=0.05, allow_unverified="assemblyai-stream",
                           console=Console(record=True))  # fmt: skip
    assert res.rows.iloc[0]["status"] == "ok"
    meta = json.loads(res.meta_path.read_text())
    assert "policy_status is unverified" in meta["allow_unverified"]["assemblyai-stream"]
    assert meta["verification_refused"] == {}
    pm = meta["providers"][0]
    assert (pm["price_status"], pm["policy_status"], pm["live_verified"]) == ("documented", "unverified", False)
