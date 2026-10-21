"""Verification metadata in pricing.toml and the fail-closed gate for real runs. Mocked transports only."""

from __future__ import annotations

import json
from typing import ClassVar

import httpx
import pytest
from rich.console import Console

from clipbench.providers.base import load_pricing
from clipbench.providers.deepgram import Deepgram
from clipbench.providers.fake import FakeProvider
from clipbench.providers.groq import GroqWhisper
from clipbench.providers.http import no_sleep_policy
from clipbench.providers.registry import REGISTRY, provider_class
from clipbench.providers.verification import (
    POLICY_STATUSES,
    PRICE_STATUSES,
    check,
    is_gated,
    parse_allow,
    refusal_reason,
    verification,
)
from clipbench.runner import UnverifiedProviders, run
from clipbench.streaming.fake import FakeStream
from clipbench.streaming.registry import STREAM_REGISTRY
from tests.fixtures.make_clips import make_clips

FREE = {"fake", "whisper-local", "whisper-local-turbo", "youtube-auto", "wayin"}
# What pricing.toml says today (2026-10-21). Changing a status is a deliberate edit, so this list changes too.
REFUSED_BY_DEFAULT = {
    "groq-whisper": "policy_status is unknown",
    "aws-transcribe": "price_status is unverified",
    "cf-whisper-tiny-en": "price_status is unverified",
    "assemblyai-stream": "policy_status is unverified",
    "elevenlabs-stream": "policy_status is unverified",
}

DG_BODY = {
    "metadata": {"duration": 3.0},
    "results": {"channels": [{"alternatives": [{"transcript": "こんにちは", "words": [
        {"word": "こんにちは", "start": 0.1, "end": 0.6}]}]}]},
}  # fmt: skip


@pytest.fixture
def clips(tmp_path):
    return make_clips(tmp_path / "audio")[:2]


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-dg")


def mocked(cls, body):
    return cls(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), retry=no_sleep_policy())


# ---------------------------------------------------------------- metadata


def test_every_pricing_entry_has_verification_fields():
    for pid, entry in load_pricing().items():
        assert entry.get("live_verified") is False, f"{pid}: nothing has been verified live yet"
        assert entry.get("price_status") in PRICE_STATUSES, pid
        assert entry.get("policy_status") in POLICY_STATUSES, pid
        assert isinstance(entry.get("unverified"), list), pid
        assert all(isinstance(q, str) and q.strip() for q in entry["unverified"]), pid


def test_every_registered_provider_has_an_entry():
    ids = (set(REGISTRY) | set(STREAM_REGISTRY)) - {"fake-stream"}
    assert ids <= set(load_pricing())


def test_free_systems_are_not_applicable_and_paid_ones_list_open_questions():
    for pid, entry in load_pricing().items():
        if pid in FREE:
            assert (entry["price_status"], entry["policy_status"]) == ("not_applicable", "not_applicable")
        else:
            assert entry["price_status"] != "not_applicable" and entry["policy_status"] != "not_applicable"
            assert entry["unverified"], f"{pid}: a paid provider that was never called live has open questions"


def test_unverified_items_carry_no_dashes():
    for pid, entry in load_pricing().items():
        for q in entry["unverified"]:
            assert "\u2014" not in q and "\u2013" not in q, pid


def test_verification_reads_pricing():
    v = verification("aws-transcribe")
    assert v.price_status == "unverified" and v.policy_status == "documented" and not v.live_verified
    assert any("0.024" in q for q in v.unverified)
    assert verification("nope").price_status is None


# ---------------------------------------------------------------- gate


def test_default_refusals_match_pricing():
    ids = [p for p in list(REGISTRY) + list(STREAM_REGISTRY)]
    got = {}
    for pid in ids:
        cls = provider_class(pid) if pid in REGISTRY else None
        if cls is None:
            from clipbench.streaming.registry import provider_class as stream_class

            cls = stream_class(pid)
        if pid == "aws-transcribe":
            p = cls(s3_client=object(), transcribe_client=object())
        else:
            p = cls()
        reason = refusal_reason(p)
        if reason:
            got[pid] = reason
    assert set(got) == set(REFUSED_BY_DEFAULT)
    for pid, fragment in REFUSED_BY_DEFAULT.items():
        assert fragment in got[pid]


def test_free_and_fake_providers_are_exempt():
    class PaidFake(FakeProvider):
        paid: ClassVar[bool] = True

    class PaidFakeStream(FakeStream):
        paid: ClassVar[bool] = True

    for p in (FakeProvider(), PaidFake(cost_per_minute=1.0), FakeStream(), PaidFakeStream()):
        assert not is_gated(p) and refusal_reason(p) is None
    from clipbench.providers.youtube_auto import YouTubeAuto

    assert refusal_reason(YouTubeAuto()) is None


def test_unregistered_paid_adapter_is_outside_the_gate():
    """Test doubles and the synthetic fixture generator have no pricing.toml entry to check."""

    class Synth(FakeProvider):
        id: ClassVar[str] = "synth-x"
        paid: ClassVar[bool] = True
        verification_exempt: ClassVar[bool] = False

    assert not is_gated(Synth(cost_per_minute=1.0))


def test_registered_paid_provider_without_price_is_refused(monkeypatch):
    real = load_pricing()
    patched = {**real, "deepgram": {k: v for k, v in real["deepgram"].items() if k not in ("usd_per_minute", "models")}}
    monkeypatch.setattr("clipbench.providers.verification.load_pricing", lambda: patched)
    monkeypatch.setattr("clipbench.providers.base.load_pricing", lambda: patched)
    reason = refusal_reason(Deepgram())
    assert reason and "no price" in reason


def test_missing_status_fields_fail_closed(monkeypatch):
    real = load_pricing()
    entry = {k: v for k, v in real["deepgram"].items() if k not in ("price_status", "policy_status")}
    monkeypatch.setattr("clipbench.providers.verification.load_pricing", lambda: {**real, "deepgram": entry})
    reason = refusal_reason(Deepgram())
    assert "price_status is missing" in reason and "policy_status is missing" in reason


def test_check_splits_refused_and_overridden():
    refused, overridden = check([GroqWhisper(), Deepgram()], parse_allow("groq-whisper"))
    assert refused == {} and set(overridden) == {"groq-whisper"}
    refused, overridden = check([GroqWhisper(), Deepgram()], ())
    assert set(refused) == {"groq-whisper"} and overridden == {}


def test_parse_allow():
    assert parse_allow(None) == frozenset()
    assert parse_allow(" groq-whisper, aws-transcribe ,") == {"groq-whisper", "aws-transcribe"}
    assert parse_allow(["a", ""]) == {"a"}


# ---------------------------------------------------------------- runner


async def test_dry_run_prices_refused_provider_and_marks_it(tmp_settings, clips, keys):
    console = Console(record=True, width=250)
    res = await run([mocked(GroqWhisper, {}), mocked(Deepgram, DG_BODY)], "verified", clips=clips,
                    settings=tmp_settings, progress=False, dry_run=True, stability=False, console=console)  # fmt: skip
    assert set(res.estimate.providers) == {"groq-whisper", "deepgram"}  # still priced
    assert res.estimate.providers["groq-whisper"].total_usd > 0
    assert set(res.refused_providers) == {"groq-whisper"}
    out = console.export_text()
    assert "would be refused: policy_status is unknown" in out
    assert "a real run would refuse groq-whisper" in out


async def test_dry_run_marks_locked_providers(tmp_settings, clips, monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "CLIPBENCH_AWS_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    console = Console(record=True, width=250)
    await run(["aws-transcribe"], "verified", clips=clips, settings=tmp_settings, progress=False, dry_run=True,
              stability=False, console=console)  # fmt: skip
    out = console.export_text()
    assert "if you add the missing keys" in out and "would be refused: price_status is unverified" in out


async def test_real_run_refuses_unverified_provider(tmp_settings, clips, keys):
    groq = mocked(GroqWhisper, {"text": "x", "words": []})
    dg = mocked(Deepgram, DG_BODY)
    res = await run([groq, dg], "verified", clips=clips, settings=tmp_settings, progress=False,
                    stability=False, run_id="r-gate", console=Console(record=True))  # fmt: skip
    assert set(res.rows["provider"]) == {"deepgram"}
    assert "--allow-unverified groq-whisper" in res.skipped_providers["groq-whisper"]
    meta = json.loads(res.meta_path.read_text())
    assert set(meta["verification_refused"]) == {"groq-whisper"}
    assert meta["allow_unverified"] == {}
    dg_meta = next(m for m in meta["providers"] if m["id"] == "deepgram")
    assert (dg_meta["price_status"], dg_meta["policy_status"], dg_meta["live_verified"]) == (
        "documented", "documented", False,
    )  # fmt: skip


async def test_real_run_with_only_refused_providers_does_not_start(tmp_settings, clips, keys):
    with pytest.raises(UnverifiedProviders, match="groq-whisper"):
        await run([mocked(GroqWhisper, {})], "verified", clips=clips, settings=tmp_settings, progress=False,
                  stability=False, run_id="r-none", console=Console(record=True))  # fmt: skip
    assert not (tmp_settings.runs_dir / "r-none").exists()


async def test_override_runs_and_is_recorded(tmp_settings, clips, keys):
    groq = mocked(GroqWhisper, {"text": "x", "words": []})
    res = await run([groq], "verified", clips=clips, settings=tmp_settings, progress=False, stability=False,
                    run_id="r-allow", allow_unverified="groq-whisper", console=Console(record=True))  # fmt: skip
    assert set(res.rows["status"]) == {"ok"}
    meta = json.loads(res.meta_path.read_text())
    assert "policy_status is unknown" in meta["allow_unverified"]["groq-whisper"]
    assert meta["verification_refused"] == {}
    assert next(m for m in meta["providers"] if m["id"] == "groq-whisper")["policy_status"] == "unknown"


def test_cli_allow_unverified_validates_ids(tmp_settings):
    from typer.testing import CliRunner

    from clipbench.cli import app

    out = CliRunner().invoke(app, ["run", "--providers", "fake", "--dry-run", "--allow-unverified", "nope"])
    assert out.exit_code != 0 and "nope" in out.output
    out = CliRunner().invoke(app, ["run-stream", "--providers", "fake-stream", "--dry-run", "--allow-unverified",
                                   "deepgram"])  # fmt: skip
    assert out.exit_code != 0


# ---------------------------------------------------------------- streaming runner


async def test_streaming_gate(tmp_settings, tmp_path, monkeypatch):
    import numpy as np
    import soundfile

    from clipbench.models import Clip, Reference
    from clipbench.streaming.assemblyai import AssemblyAIStream
    from clipbench.streaming.runner import UnverifiedProviders as StreamUnverified
    from clipbench.streaming.runner import run_stream

    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "test")
    wav = tmp_path / "a.wav"
    soundfile.write(str(wav), np.zeros(8000, dtype="float32"), 16000)
    ref = Reference(video_id="a", language="en", tier="verified", text="hi")
    clip = Clip(video_id="a", language="en", duration_s=0.5, audio_path=str(wav), audio_sha256="sha-a", reference=ref)
    console = Console(record=True, width=250)
    res = await run_stream([AssemblyAIStream()], "verified", clips=[clip], settings=tmp_settings, dry_run=True,
                           console=console)  # fmt: skip
    assert res.estimate.providers["assemblyai-stream"].total_usd > 0
    assert "would be refused: policy_status is unverified" in console.export_text()
    with pytest.raises(StreamUnverified, match="assemblyai-stream"):
        await run_stream([AssemblyAIStream()], "verified", clips=[clip], settings=tmp_settings,
                         console=Console(record=True))  # fmt: skip
    res = await run_stream([AssemblyAIStream()], "verified", clips=[clip], settings=tmp_settings, dry_run=True,
                           allow_unverified=["assemblyai-stream"], console=Console(record=True))  # fmt: skip
    assert res.refused_providers == {} and set(res.allowed_unverified) == {"assemblyai-stream"}


def test_docs_verification_table_matches_pricing():
    """docs/providers.md mirrors pricing.toml: same statuses, and the refused column matches the gate."""
    import re
    from pathlib import Path

    doc = (Path(__file__).parents[1] / "docs" / "providers.md").read_text()
    section = doc.split("## Verification status", 1)[1].split("### First live test", 1)[0]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"\| `([a-z0-9-]+)` \| \**(\w+)\** \| \**(\w+)\** \| (\w+) \| (\w+) \|", line)
        if m:
            rows[m.group(1)] = m.groups()[1:]
    paid = [pid for pid in load_pricing() if pid not in FREE]
    assert set(rows) == set(paid)
    for pid, (price, policy, live, verdict) in rows.items():
        v = verification(pid)
        assert (price, policy, live) == (v.price_status, v.policy_status, "no" if not v.live_verified else "yes"), pid
        assert (verdict == "refused") == (pid in REFUSED_BY_DEFAULT), pid
