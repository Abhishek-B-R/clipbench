"""Each streaming adapter against an in-memory fake server speaking that provider's documented protocol.
No socket is opened and no provider is contacted."""

from __future__ import annotations

import base64
import json

import pytest

from clipbench.streaming.assemblyai import AssemblyAIStream
from clipbench.streaming.base import FakeClock, StreamError
from clipbench.streaming.cloudflare import CfNova3Stream
from clipbench.streaming.deepgram import DeepgramStream
from clipbench.streaming.elevenlabs import ElevenLabsStream
from clipbench.streaming.fakeserver import memory_connect
from clipbench.streaming.gemini import GeminiStream
from clipbench.streaming.metrics import final_text, final_words
from clipbench.streaming.pacer import Pacer
from clipbench.streaming.session import stream_audio

PCM = b"\x01\x00" * int(16000 * 0.3)  # 0.3 s -> three 100 ms chunks
AUDIO_END = 0.3
FINAL_AT = 0.55  # the fake servers send their last final at this clock time


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for k in ("DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY",
              "CLOUDFLARE_API_TOKEN"):  # fmt: skip
        monkeypatch.setenv(k, "test-key")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")


async def run(provider, handler, language="ja", quiet_s=None):
    clock = FakeClock()
    connect, server = memory_connect(lambda srv, url, headers: handler(srv, clock))
    t = await stream_audio(provider, PCM, language, pacer=Pacer(chunk_ms=100, clock=clock), connect=connect,
                           quiet_s=quiet_s, final_timeout_s=2.0)  # fmt: skip
    assert not server.errors
    return t, server.connections[0]


# ---------------------------------------------------------------- Deepgram


def dg_results(text, is_final, words=None):
    return json.dumps(
        {"type": "Results", "is_final": is_final, "speech_final": is_final, "start": 0.0, "duration": 0.3,
         "channel": {"alternatives": [{"transcript": text, "words": words or []}]},
         "metadata": {"model_info": {"name": "nova-3", "version": "2026-01-01"}}}
    )  # fmt: skip


async def deepgram_server(srv, clock):
    n = 0
    while True:
        msg = await srv.recv()
        if isinstance(msg, bytes):
            n += 1
            if n == 1:
                await srv.send(dg_results("こん", False))
            elif n == 2:
                await srv.send(dg_results("こんにち", False))
            continue
        assert json.loads(msg) == {"type": "CloseStream"}
        clock.set(FINAL_AT)
        words = [{"word": "こんにちは", "punctuated_word": "こんにちは。", "start": 0.04, "end": 0.28}]
        await srv.send(dg_results("こんにちは。", True, words))
        await srv.send(dg_results("", True))  # an empty final closes a segment and is dropped
        await srv.send(json.dumps({"type": "Metadata", "request_id": "r1", "duration": 0.3}))
        return


async def test_deepgram():
    p = DeepgramStream()
    t, conn = await run(p, deepgram_server)
    assert conn["url"].startswith("wss://api.deepgram.com/v1/listen?")
    for part in ("model=nova-3", "language=ja", "encoding=linear16", "sample_rate=16000", "interim_results=true",
                 "mip_opt_out=true"):  # fmt: skip
        assert part in conn["url"]
    assert conn["headers"]["authorization"] == "Token test-key"
    assert sum(isinstance(m, bytes) for m in conn["server"].received) == 3
    assert [e.kind for e in t.events] == ["partial", "partial", "final"]
    assert final_text(t.events) == "こんにちは。"
    assert final_words(t.events)[0].model_dump() == {"text": "こんにちは。", "start_ms": 40, "end_ms": 280}
    assert t.events[-1].t_ms == pytest.approx(FINAL_AT * 1000)
    assert t.audio_end_ms == pytest.approx(AUDIO_END * 1000)
    assert t.meta["model_version"] == "nova-3:2026-01-01"
    assert t.meta["billed_audio_s"] == 0.3
    assert not t.timed_out
    assert p.actual_cost(t) == pytest.approx(p.price_per_minute() * 0.3 / 60)


async def test_deepgram_error_message_raises():
    async def server(srv, clock):
        await srv.recv()
        await srv.send(json.dumps({"type": "Error", "description": "bad language"}))
        await srv.recv()

    with pytest.raises(StreamError, match="bad language"):
        await run(DeepgramStream(), server)


async def test_server_closing_early_is_an_error():
    async def server(srv, clock):
        await srv.recv()
        await srv.close()

    with pytest.raises(StreamError):
        await run(DeepgramStream(), server)


async def test_cloudflare_nova3_uses_deepgram_messages():
    t, conn = await run(CfNova3Stream(), deepgram_server, language="hi")
    assert conn["url"].startswith("wss://api.cloudflare.com/client/v4/accounts/acct123/ai/run/@cf/deepgram/nova-3?")
    assert "model=" not in conn["url"] and "language=hi" in conn["url"]
    assert conn["headers"]["authorization"] == "Bearer test-key"
    assert final_text(t.events) == "こんにちは。"


# ---------------------------------------------------------------- AssemblyAI


def turn(order, words, end, transcript=""):
    return json.dumps({"type": "Turn", "turn_order": order, "end_of_turn": end, "turn_is_formatted": end,
                       "transcript": transcript, "words": words})  # fmt: skip


async def assemblyai_server(srv, clock):
    await srv.send(json.dumps({"type": "Begin", "id": "s1", "configuration": {"model": "universal-3-6-pro"}}))
    n = 0
    while True:
        msg = await srv.recv()
        if isinstance(msg, bytes):
            n += 1
            if n == 2:
                await srv.send(turn(0, [{"text": "hola", "start": 10, "end": 200, "word_is_final": True},
                                        {"text": "mun", "start": 210, "end": 260, "word_is_final": False}],
                                    False, "hola"))  # fmt: skip
            continue
        assert json.loads(msg) == {"type": "Terminate"}
        clock.set(FINAL_AT)
        words = [{"text": "Hola", "start": 10, "end": 200, "word_is_final": True},
                 {"text": "mundo.", "start": 210, "end": 290, "word_is_final": True}]  # fmt: skip
        await srv.send(turn(0, words, True, "Hola mundo."))
        await srv.send(json.dumps({"type": "Termination", "audio_duration_seconds": 0.3,
                                   "session_duration_seconds": 1.2}))  # fmt: skip
        return


async def test_assemblyai():
    p = AssemblyAIStream()
    t, conn = await run(p, assemblyai_server, language="es")
    url = conn["url"]
    assert url.startswith("wss://streaming.assemblyai.com/v3/ws?")
    for part in ("speech_model=universal-3-6-pro", "encoding=pcm_s16le", "sample_rate=16000", "language_codes=es"):
        assert part in url
    assert conn["headers"]["authorization"] == "test-key"
    assert [(e.kind, e.text) for e in t.events] == [("partial", "hola mun"), ("final", "Hola mundo.")]
    assert [w.start_ms for w in final_words(t.events)] == [10, 210]
    assert t.meta["configuration"] == {"model": "universal-3-6-pro"}
    assert t.meta["billed_session_s"] == 1.2
    # session billing: 1.2 s of open socket, not 0.3 s of audio
    assert p.actual_cost(t) == pytest.approx(0.0075 * 1.2 / 60)


# ---------------------------------------------------------------- ElevenLabs


async def elevenlabs_server(srv, clock):
    await srv.send(json.dumps({"message_type": "session_started", "session_id": "e1"}))
    n = 0
    while True:
        msg = json.loads(await srv.recv())
        assert msg["message_type"] == "input_audio_chunk" and msg["sample_rate"] == 16000
        if not msg["commit"]:
            assert len(base64.b64decode(msg["audio_base_64"])) == 3200
            n += 1
            if n == 1:
                await srv.send(json.dumps({"message_type": "partial_transcript", "text": "안녕"}))
            continue
        assert msg["audio_base_64"] == ""
        clock.set(FINAL_AT)
        await srv.send(json.dumps({"message_type": "committed_transcript", "text": "안녕하세요"}))
        await srv.send(json.dumps({
            "message_type": "committed_transcript_with_timestamps", "text": "안녕하세요", "language_code": "ko",
            "words": [{"text": "안녕하세요", "start": 0.05, "end": 0.29, "type": "word"},
                      {"text": " ", "start": 0.29, "end": 0.3, "type": "spacing"}]}))  # fmt: skip
        await srv.recv()  # stays open: the client ends the session after the quiet period


async def test_elevenlabs():
    t, conn = await run(ElevenLabsStream(), elevenlabs_server, language="ko", quiet_s=0.1)
    url = conn["url"]
    assert url.startswith("wss://api.elevenlabs.io/v1/speech-to-text/realtime?")
    for part in ("model_id=scribe_v2_realtime", "audio_format=pcm_16000", "language_code=ko",
                 "commit_strategy=vad", "include_timestamps=true"):  # fmt: skip
        assert part in url
    assert conn["headers"]["xi-api-key"] == "test-key"
    assert [(e.kind, e.text) for e in t.events] == [("partial", "안녕"), ("final", "안녕하세요")]
    assert [w.model_dump() for w in final_words(t.events)] == [{"text": "안녕하세요", "start_ms": 50, "end_ms": 290}]
    assert t.meta["session_id"] == "e1"
    assert not t.timed_out


async def test_elevenlabs_error():
    async def server(srv, clock):
        await srv.send(json.dumps({"message_type": "auth_error", "error": "invalid key"}))
        await srv.recv()

    with pytest.raises(StreamError, match="invalid key"):
        await run(ElevenLabsStream(), server)


# ---------------------------------------------------------------- Gemini


async def gemini_server(srv, clock):
    setup = json.loads(await srv.recv())
    assert setup == {"setup": {"model": "models/gemini-3.5-transcribe-live",
                               "generationConfig": {"responseModalities": ["TEXT"]},
                               "inputAudioTranscription": {"languageCodes": ["es-419"]}}}  # fmt: skip
    await srv.send(json.dumps({"setupComplete": {}}))
    n = 0
    while True:
        msg = json.loads(await srv.recv())
        if "audio" in msg["realtimeInput"]:
            assert msg["realtimeInput"]["audio"]["mimeType"] == "audio/pcm;rate=16000"
            n += 1
            if n == 2:
                await srv.send(json.dumps({"serverContent": {"interimInputTranscription": {"text": "buenos"}}}))
            continue
        assert msg == {"realtimeInput": {"audioStreamEnd": True}}
        clock.set(FINAL_AT)
        await srv.send(json.dumps({"serverContent": {"inputTranscription": {"text": "Buenos días."}}}))
        await srv.send(json.dumps({"usageMetadata": {"promptTokenCount": 8}}))
        await srv.recv()


async def test_gemini():
    t, conn = await run(GeminiStream(), gemini_server, language="es", quiet_s=0.1)
    assert conn["url"].endswith("GenerativeService.BidiGenerateContent")
    assert "key=" not in conn["url"]
    assert conn["headers"]["x-goog-api-key"] == "test-key"
    assert [(e.kind, e.text) for e in t.events] == [("partial", "buenos"), ("final", "Buenos días.")]
    assert final_words(t.events) is None  # no word timestamps over the Live API
    assert t.meta["usage"] == {"promptTokenCount": 8}


async def test_quiet_provider_times_out_without_any_message():
    async def server(srv, clock):
        await srv.recv()
        await srv.send(json.dumps({"setupComplete": {}}))
        while True:
            await srv.recv()

    clock = FakeClock()
    connect, _ = memory_connect(lambda srv, url, headers: server(srv, clock))
    p = GeminiStream()
    t = await stream_audio(p, PCM, "es", pacer=Pacer(clock=clock), connect=connect, quiet_s=None,
                           final_timeout_s=0.2)  # fmt: skip
    # quiet_s=None falls back to the class default (3 s), so the 0.2 s final timeout fires first.
    assert t.timed_out


def test_language_and_length_checks():
    from clipbench.providers.base import ProviderSkip

    with pytest.raises(ProviderSkip):
        DeepgramStream().check_clip("fr", 10)
    with pytest.raises(ProviderSkip):
        GeminiStream().check_clip("ja", 601)
    GeminiStream().check_clip("ja-JP", 30)


def test_settings_hash_is_stream_specific():
    p = DeepgramStream()
    assert p.settings()["kind"] == "stream"
    q = DeepgramStream()
    q.options["chunk_ms"] = 50
    assert p.settings_hash() != q.settings_hash()
