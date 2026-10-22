"""Run options (diarize, detect_language, keyterms) per adapter. Mocked transports only."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
import pytest
import soundfile

from clipbench.providers.assemblyai import AssemblyAI
from clipbench.providers.aws_transcribe import AwsTranscribe, parse_transcript
from clipbench.providers.cloudflare import CfDeepgramNova3, CfWhisper, CfWhisperLargeV3Turbo
from clipbench.providers.deepgram import Deepgram
from clipbench.providers.elevenlabs import ElevenLabsScribe, valid_keyterms
from clipbench.providers.gemini import GeminiFlash, parse_detect_reply
from clipbench.providers.groq import GroqWhisper, keyterm_prompt
from clipbench.providers.http import no_sleep_policy
from clipbench.providers.whisper_local import WhisperLocal

KEYS = {
    "GROQ_API_KEY": "k",
    "DEEPGRAM_API_KEY": "k",
    "ASSEMBLYAI_API_KEY": "k",
    "ELEVENLABS_API_KEY": "k",
    "GEMINI_API_KEY": "k",
    "CLOUDFLARE_ACCOUNT_ID": "acct",
    "CLOUDFLARE_API_TOKEN": "tok",
}
ALL = {"diarize": True, "detect_language": True, "keyterms": ["Acme", "Zorblax Pro"]}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CLIPBENCH_ELEVENLABS_ZERO_RETENTION", raising=False)


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "clip.wav"
    soundfile.write(str(p), np.zeros(16000 * 2, dtype="float32"), 16000)
    return p


def make(cls, handler, **kw):
    return cls(transport=httpx.MockTransport(handler), retry=no_sleep_policy(2), **kw)


def query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query)


def multipart_fields(request: httpx.Request) -> list[tuple[str, str]]:
    """(name, value) of every non file form field of a multipart body."""
    body = request.read().decode("utf-8", errors="replace")
    out = []
    for part in body.split("Content-Disposition: form-data; ")[1:]:
        head, _, rest = part.partition("\r\n\r\n")
        if "filename=" in head:
            continue
        name = head.split('name="', 1)[1].split('"', 1)[0]
        out.append((name, rest.rsplit("\r\n--", 1)[0]))
    return out


# ------------------------------------------------------------------ deepgram

DG_BODY = {
    "metadata": {"duration": 2.0},
    "results": {
        "channels": [
            {
                "detected_language": "ja",
                "language_confidence": 0.98,
                "alternatives": [
                    {
                        "transcript": "こんにちは 世界",
                        "words": [
                            {"word": "こんにちは", "start": 0.0, "end": 0.5, "speaker": 0},
                            {"word": "世界", "start": 0.6, "end": 1.0, "speaker": 1},
                        ],
                    }
                ],
            }
        ]
    },
}


async def test_deepgram_all_options(wav):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json=DG_BODY)

    r = await make(Deepgram, h, **ALL).transcribe(wav, "ja")
    q = query(seen[0])
    assert "language" not in q and q["detect_language"] == ["true"]
    assert q["diarize_model"] == ["v2"]
    assert q["keyterm"] == ["Acme", "Zorblax Pro"]
    assert r.speakers == ["0", "1"]
    assert r.detected_language == "ja"
    assert "clipbench_unsupported" not in r.raw_response


async def test_deepgram_defaults_send_language_only(wav):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json=DG_BODY)

    r = await make(Deepgram, h).transcribe(wav, "ja")
    q = query(seen[0])
    assert q["language"] == ["ja"]
    assert not {"detect_language", "diarize_model", "keyterm", "diarize"} & set(q)
    assert r.speakers == ["0", "1"]  # labels in the response are kept even if not requested
    assert r.detected_language is None  # only reported on detect_language runs


# ------------------------------------------------------------------ assemblyai

AAI_DONE = {
    "id": "t1",
    "status": "completed",
    "text": "hola mundo",
    "language_code": "es",
    "speech_model_used": "universal-3-5-pro",
    "audio_duration": 2,
    "words": [
        {"text": "hola", "start": 0, "end": 400, "speaker": "A"},
        {"text": "mundo", "start": 500, "end": 900, "speaker": "B"},
    ],
}


def aai_handler(seen):
    def h(req):
        seen.append(req)
        if req.url.path.endswith("/upload"):
            return httpx.Response(200, json={"upload_url": "https://cdn/x"})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "t1", "status": "queued"})
        if req.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=AAI_DONE)

    return h


async def test_assemblyai_all_options(wav):
    seen = []
    terms = ["Acme", "one two three four five six seven"] + [f"t{i}" for i in range(300)]
    r = await make(AssemblyAI, aai_handler(seen), diarize=True, detect_language=True, keyterms=terms).transcribe(
        wav, "es"
    )
    body = json.loads(seen[1].content)
    assert "language_code" not in body and body["language_detection"] is True
    assert body["speaker_labels"] is True
    assert body["keyterms_prompt"][0] == "Acme"
    assert len(body["keyterms_prompt"]) == 200  # capped for the Universal-2 fallback
    assert all(len(t.split()) <= 6 for t in body["keyterms_prompt"])
    assert r.speakers == ["A", "B"]
    assert r.detected_language == "es"


async def test_assemblyai_defaults(wav):
    seen = []
    r = await make(AssemblyAI, aai_handler(seen)).transcribe(wav, "es")
    body = json.loads(seen[1].content)
    assert body["language_code"] == "es"
    assert not {"language_detection", "speaker_labels", "keyterms_prompt"} & set(body)
    assert r.speakers is None and r.detected_language is None


# ------------------------------------------------------------------ elevenlabs

EL_BODY = {
    "text": "hello there",
    "language_code": "eng",
    "language_probability": 0.99,
    "words": [
        {"text": "hello", "type": "word", "start": 0.0, "end": 0.4, "speaker_id": "speaker_0"},
        {"text": " ", "type": "spacing", "start": 0.4, "end": 0.5, "speaker_id": "speaker_0"},
        {"text": "there", "type": "word", "start": 0.5, "end": 0.9, "speaker_id": "speaker_1"},
    ],
}


async def test_elevenlabs_all_options(wav):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json=EL_BODY)

    p = make(ElevenLabsScribe, h, **ALL)
    r = await p.transcribe(wav, "en")
    fields = multipart_fields(seen[0])
    names = [n for n, _ in fields]
    assert "language_code" not in names
    assert ("diarize", "true") in fields
    assert [v for n, v in fields if n == "keyterms"] == ["Acme", "Zorblax Pro"]
    assert r.speakers == ["speaker_0", "speaker_1"]
    assert r.detected_language == "en"
    plain = ElevenLabsScribe().estimate_cost(60)
    assert p.estimate_cost(60) == pytest.approx(plain * 1.2)  # keyterm surcharge


def test_elevenlabs_keyterm_rules():
    terms = ["ok", "x" * 50, "a b c d e f", "bad[term]", "fine term"]
    assert valid_keyterms(terms) == ["ok", "fine term"]
    many = ElevenLabsScribe(keyterms=[f"t{i}" for i in range(101)])
    assert many.estimate_cost(5) == pytest.approx(ElevenLabsScribe().estimate_cost(20) * 1.2)


# ------------------------------------------------------------------ groq

async def test_groq_detect_and_prompt(wav):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"text": "hola", "language": "spanish", "words": []})

    r = await make(GroqWhisper, h, **ALL).transcribe(wav, "es")
    fields = dict(multipart_fields(seen[0]))
    assert "language" not in fields
    assert fields["prompt"] == "Acme, Zorblax Pro"
    assert r.detected_language == "es"
    assert r.raw_response["clipbench_unsupported"] == ["diarize"]
    assert r.speakers is None


def test_keyterm_prompt_keeps_whole_terms():
    assert keyterm_prompt(["aaaa", "bbbb", "cccc"], max_chars=10) == "aaaa, bbbb"
    assert keyterm_prompt([]) == ""


# ------------------------------------------------------------------ gemini

async def test_gemini_detect_language_structured(wav):
    seen = []
    reply = {"candidates": [{"content": {"parts": [{"text": '{"language": "ko", "transcript": "안녕"}'}]}}]}

    def h(req):
        seen.append(req)
        return httpx.Response(200, json=reply)

    r = await make(GeminiFlash, h, **ALL).transcribe(wav, "ko")
    body = json.loads(seen[0].content)
    prompt = body["contents"][0]["parts"][0]["text"]
    assert "Korean" not in prompt and "Acme; Zorblax Pro" in prompt
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert r.text == "안녕" and r.detected_language == "ko"
    assert r.raw_response["clipbench_unsupported"] == ["diarize"]


def test_gemini_detect_reply_fallback():
    assert parse_detect_reply("not json") == ("not json", None)
    assert parse_detect_reply('{"language": "ja-JP", "transcript": " x "}') == ("x", "ja")


# ------------------------------------------------------------------ cloudflare

def cf_handler(seen, result):
    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"success": True, "result": result, "errors": []})

    return h


async def test_cf_turbo_detect_and_prompt(wav):
    seen = []
    result = {
        "text": "hola",
        "transcription_info": {"language": "es", "language_probability": 0.9},
        "segments": [{"words": [{"word": "hola", "start": 0.0, "end": 0.3}]}],
    }
    r = await make(CfWhisperLargeV3Turbo, cf_handler(seen, result), **ALL).transcribe(wav, "es")
    body = json.loads(seen[0].content)
    assert "language" not in body and body["initial_prompt"] == "Acme, Zorblax Pro"
    assert base64.b64decode(body["audio"])
    assert r.detected_language == "es"
    assert r.raw_response["clipbench_unsupported"] == ["diarize"]


async def test_cf_turbo_hinted_has_no_detected_language(wav):
    seen = []
    result = {"text": "hola", "transcription_info": {"language": "es"}, "segments": []}
    r = await make(CfWhisperLargeV3Turbo, cf_handler(seen, result)).transcribe(wav, "es")
    assert json.loads(seen[0].content)["language"] == "es"
    assert r.detected_language is None


async def test_cf_whisper_records_everything_unsupported(wav):
    seen = []
    r = await make(CfWhisper, cf_handler(seen, {"text": "hi", "words": []}), **ALL).transcribe(wav, "en")
    assert r.raw_response["clipbench_unsupported"] == ["detect_language", "diarize", "keyterms"]


async def test_cf_nova3_options(wav):
    seen = []
    r = await make(CfDeepgramNova3, cf_handler(seen, DG_BODY), **ALL).transcribe(wav, "ja")
    q = query(seen[0])
    assert "language" not in q and q["detect_language"] == ["true"]
    assert q["diarize"] == ["true"]
    assert q["keyterm"] == ["Acme", "Zorblax Pro"]
    assert r.speakers == ["0", "1"] and r.detected_language == "ja"


# ------------------------------------------------------------------ aws

AWS_BODY = {
    "results": {
        "language_code": "ko-KR",
        "transcripts": [{"transcript": "안녕 하세요"}],
        "speaker_labels": {
            "segments": [
                {"speaker_label": "spk_0", "items": [{"start_time": "0.1", "speaker_label": "spk_0"}]},
                {"speaker_label": "spk_1", "items": [{"start_time": "0.5", "speaker_label": "spk_1"}]},
            ]
        },
        "items": [
            {"type": "pronunciation", "start_time": "0.1", "end_time": "0.4", "alternatives": [{"content": "안녕"}]},
            {"type": "pronunciation", "start_time": "0.5", "end_time": "0.9", "alternatives": [{"content": "하세요"}]},
        ],
    }
}


def test_aws_job_args():
    p = AwsTranscribe(s3_client=object(), transcribe_client=object(), **ALL)
    args = p.job_args("ko")
    assert args["IdentifyLanguage"] is True and "LanguageCode" not in args
    assert set(args["LanguageOptions"]) == {"en-US", "es-US", "fr-FR", "hi-IN", "ja-JP", "ko-KR"}
    assert args["Settings"] == {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 10}
    assert AwsTranscribe(s3_client=object(), transcribe_client=object()).job_args("hi") == {
        "LanguageCode": "hi-IN"
    }
    assert p.unsupported_options("ko") == ["keyterms"]


def test_aws_parse_speakers_and_language():
    p = AwsTranscribe(s3_client=object(), transcribe_client=object(), diarize=True, detect_language=True)
    r = parse_transcript(AWS_BODY, p, 1.0, 10.0, job={"LanguageCode": "ko-KR"})
    assert r.speakers == ["spk_0", "spk_1"]
    assert r.detected_language == "ko"
    plain = parse_transcript(AWS_BODY, AwsTranscribe(s3_client=object(), transcribe_client=object()), 1.0, 1.0)
    assert plain.speakers is None and plain.detected_language is None


# ------------------------------------------------------------------ whisper local

def test_whisper_local_decode_options():
    p = WhisperLocal(backend="mlx", model_id="m", detect_language=True, keyterms=["Acme"])
    assert p.decode_options("ja") == {"language": None, "initial_prompt": "Acme"}
    assert WhisperLocal(backend="mlx", model_id="m").decode_options("ja") == {"language": "ja"}
    assert WhisperLocal(backend="mlx", model_id="m", diarize=True).unsupported_options("ja") == ["diarize"]


def test_options_are_in_the_cache_key():
    a = Deepgram()
    b = Deepgram(keyterms=["Acme"])
    assert a.settings_hash() != b.settings_hash()
