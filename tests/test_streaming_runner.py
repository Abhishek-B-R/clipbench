"""run-stream, score-stream and the export hook, end to end on the fake provider and the in-memory server."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import soundfile
from typer.testing import CliRunner

from clipbench.budget import BudgetExceeded
from clipbench.models import Clip, Reference, Word
from clipbench.streaming.assemblyai import AssemblyAIStream
from clipbench.streaming.base import FakeClock
from clipbench.streaming.deepgram import DeepgramStream
from clipbench.streaming.export import export_section
from clipbench.streaming.fake import FakeStream, script_handler
from clipbench.streaming.fakeserver import memory_connect
from clipbench.streaming.runner import estimate_stream, run_stream
from clipbench.streaming.score import aggregate, median_ci, score_stream

TEXT = "the cat sat on the mat"


def make_clip(tmp_path, vid, seconds=0.5, text=TEXT, **ref_kw) -> Clip:
    wav = tmp_path / f"{vid}.wav"
    soundfile.write(str(wav), np.zeros(int(16000 * seconds), dtype="float32"), 16000)
    words = [Word(text=t, start_ms=i * 300, end_ms=i * 300 + 250) for i, t in enumerate(text.split())]
    ref = Reference(video_id=vid, language="en", tier="verified", text=text, words=words, **ref_kw)
    return Clip(video_id=vid, language="en", duration_s=seconds, audio_path=str(wav), audio_sha256=f"sha-{vid}",
                reference=ref)  # fmt: skip


async def fake_run(settings, clips, **kw):
    connect, _ = memory_connect(script_handler(TEXT))
    return await run_stream([FakeStream()], "verified", clips=clips, settings=settings, connect=connect,
                            clock=FakeClock(), quiet_s=0.05, **kw)  # fmt: skip


async def test_run_score_export(tmp_settings, tmp_path):
    clips = [
        make_clip(tmp_path, "a", tags=["background_music"], category="vlog_music"),
        make_clip(tmp_path, "b", category="talking_head", conditions=["fast_speech"]),
    ]
    res = await fake_run(tmp_settings, clips)
    rows = pd.read_parquet(res.rows_path)
    assert list(rows["status"]) == ["ok", "ok"]
    assert set(rows["kind"]) == {"stream"}
    assert rows["error_rate"].tolist() == [0.0, 0.0]
    assert rows["ttfs_ms"].notna().all() and rows["ttfp_ms"].notna().all()
    assert json.loads(rows.loc[rows.video_id == "a", "conditions"].iloc[0]) == ["music"]
    # timings: the fake words match the reference exactly
    assert rows["timing_within_200ms"].tolist() == [1.0, 1.0]

    run_dir = tmp_settings.runs_dir / res.run_id
    assert not (run_dir / "rows.parquet").exists()  # never mixed with batch rows
    meta = json.loads((run_dir / "run_meta.json").read_text())
    assert meta["kind"] == "stream" and meta["chunk_ms"] == 100 and meta["real_time"] is True
    # Text lives on the data side only.
    assert not any(TEXT.split()[1] in str(v) for v in rows.astype(str).to_numpy().ravel())
    texts = pd.read_parquet(tmp_settings.runs_text_dir / res.run_id / "stream_texts.parquet")
    assert texts["final_text"].tolist() == [TEXT, TEXT]
    assert (tmp_settings.runs_text_dir / res.run_id / "refs.parquet").exists()

    out = score_stream(res.run_id, tmp_settings)
    agg = pd.read_parquet(out)
    kinds = set(zip(agg["slice_kind"], agg["slice"], strict=True))
    assert {("all", "all"), ("condition", "music"), ("condition", "fast_speech"), ("condition", "no_music"),
            ("category", "vlog_music"), ("category", "talking_head")} <= kinds  # fmt: skip
    all_row = agg[agg["slice_kind"] == "all"].iloc[0]
    assert all_row["n_clips"] == 2 and all_row["error_rate"] == 0.0 and all_row["too_small"]

    section = export_section(res.run_id, tmp_settings)
    assert section["kind"] == "stream" and section["n_clips"] == 2
    prov = section["providers"][0]
    assert prov["id"] == "fake-stream" and prov["n_ok"] == 2
    assert prov["medians"]["error_rate"] == 0.0
    assert len(section["aggregates"]) == len(agg)
    json.dumps(section, allow_nan=False)  # strict JSON: no NaN
    assert TEXT not in json.dumps(section)


async def test_cache_hit_on_rerun(tmp_settings, tmp_path):
    clips = [make_clip(tmp_path, "a")]
    await fake_run(tmp_settings, clips)
    res = await fake_run(tmp_settings, clips)
    rows = pd.read_parquet(res.rows_path)
    assert rows["cached"].tolist() == [True]
    assert rows["error_rate"].tolist() == [0.0]


async def test_resume_skips_done_rows(tmp_settings, tmp_path):
    clips = [make_clip(tmp_path, "a")]
    first = await fake_run(tmp_settings, clips, no_cache=True)
    connect, server = memory_connect(script_handler(TEXT))
    await run_stream([FakeStream()], "verified", clips=clips, settings=tmp_settings, connect=connect,
                     clock=FakeClock(), quiet_s=0.05, run_id=first.run_id, no_cache=True)  # fmt: skip
    assert server.connections == []  # nothing streamed again


async def test_unsupported_language_is_skipped(tmp_settings, tmp_path):
    clip = make_clip(tmp_path, "a")
    clip = clip.model_copy(update={"language": "fr", "reference": clip.reference.model_copy(update={"language": "fr"})})
    res = await fake_run(tmp_settings, [clip])
    rows = pd.read_parquet(res.rows_path)
    assert rows["status"].tolist() == ["skipped"]


async def test_dry_run_estimate(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "x")
    clips = [make_clip(tmp_path, "a", seconds=1.0), make_clip(tmp_path, "b", seconds=2.0)]
    clips[0] = clips[0].model_copy(update={"duration_s": 30.0})
    clips[1] = clips[1].model_copy(update={"duration_s": 90.0})
    res = await run_stream([DeepgramStream(), AssemblyAIStream(), FakeStream(usd_per_minute=0.01)], "verified",
                           clips=clips, settings=tmp_settings, dry_run=True)  # fmt: skip
    est = res.estimate.providers
    # Deepgram bills audio: 2 minutes at 0.0077.
    assert est["deepgram-stream"].total_usd == pytest.approx(2 * 0.0077)
    # AssemblyAI bills the session: (30 + 3) s + (90 + 3) s at 0.0075 per minute.
    assert est["assemblyai-stream"].total_usd == pytest.approx((33 + 93) / 60 * 0.0075)
    assert est["fake-stream"].total_usd == pytest.approx(0.02)
    assert res.dry_run and res.allowed
    assert not (tmp_settings.runs_dir / res.run_id).exists()  # a dry run writes nothing


def test_estimate_speed_and_cache(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "x")
    clip = make_clip(tmp_path, "a").model_copy(update={"duration_s": 60.0})
    p = AssemblyAIStream()
    # at speed 2 the socket is open for half the audio time
    assert estimate_stream([p], [clip], speed=2.0).total_usd == pytest.approx((30 + 3) / 60 * 0.0075)


async def test_budget_refuses_to_start(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_BUDGET_USD", "0.001")
    from clipbench.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    clip = make_clip(tmp_path, "a").model_copy(update={"duration_s": 600.0})
    connect, server = memory_connect(script_handler(TEXT))
    with pytest.raises(BudgetExceeded):
        await run_stream([FakeStream(usd_per_minute=0.01)], "verified", clips=[clip], settings=settings,
                         connect=connect, clock=FakeClock())  # fmt: skip
    assert server.connections == []


async def test_actual_cost_is_recorded(tmp_settings, tmp_path):
    clips = [make_clip(tmp_path, "a", seconds=0.6)]
    connect, _ = memory_connect(script_handler(TEXT))
    res = await run_stream([FakeStream(usd_per_minute=0.5)], "verified", clips=clips, settings=tmp_settings,
                           connect=connect, clock=FakeClock(), quiet_s=0.05)  # fmt: skip
    rows = pd.read_parquet(res.rows_path)
    assert rows["cost_usd"].iloc[0] == pytest.approx(0.5 * 0.6 / 60)
    spend = json.loads(tmp_settings.spend_path.read_text())
    assert spend["by_provider"]["fake-stream"] == pytest.approx(0.5 * 0.6 / 60)


def test_aggregate_by_hand():
    rows = pd.DataFrame(
        {
            "provider": ["p", "p"], "tier": ["verified"] * 2, "language": ["en"] * 2, "status": ["ok", "ok"],
            "model_id": ["m", "m"], "video_id": ["a", "b"], "metric": ["wer", "wer"],
            "errors": [1, 3], "ref_len": [4, 6], "rewritten_tokens": [1, 1], "partial_tokens": [5, 5],
            "revisions": [2, 0], "final_tokens": [4, 6], "ttfp_ms": [100.0, 300.0], "ttfs_ms": [500.0, 700.0],
            "timing_n_words": [4, 0], "timing_within_200ms": [0.5, None], "timing_median_ms": [120.0, None],
            "cost_usd": [0.01, 0.02], "audio_seconds": [60.0, 120.0], "timed_out": [False, True],
            "speed": [1.0, 1.0], "chunk_ms": [100, 100], "conditions": ['["music"]', "[]"],
            "category": ["gaming", None],
        }
    )  # fmt: skip
    agg = aggregate(rows)
    r = agg[agg["slice_kind"] == "all"].iloc[0]
    assert r["error_rate"] == pytest.approx(4 / 10)
    assert r["ci_low"] <= r["error_rate"] <= r["ci_high"]
    assert r["partial_rewrite_rate"] == pytest.approx(2 / 10)
    assert r["revisions_per_word"] == pytest.approx(2 / 10)
    assert r["ttfp_p50_ms"] == pytest.approx(200) and r["ttfs_p50_ms"] == pytest.approx(600)
    assert r["timing_n_words"] == 4 and r["timing_within_200ms"] == pytest.approx(0.5)
    assert r["usd_per_minute"] == pytest.approx(0.03 / 3)
    assert r["timed_out_rate"] == pytest.approx(0.5)
    music = agg[(agg["slice_kind"] == "condition") & (agg["slice"] == "music")].iloc[0]
    assert music["n_clips"] == 1 and music["error_rate"] == pytest.approx(0.25)
    assert set(agg["slice"]) == {"all", "music", "no_music", "gaming"}


def test_median_ci():
    med, p90, low, high = median_ci([100.0, 200.0, 300.0])
    assert med == 200 and p90 == pytest.approx(280)
    assert 100 <= low <= med <= high <= 300
    assert all(np.isnan(x) for x in median_ci([]))


def test_export_section_none_without_aggregates(tmp_settings):
    assert export_section("nope", tmp_settings) is None


def test_cli_commands_registered(tmp_settings):
    from clipbench.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["run-stream", "--providers", "fake-stream", "--dry-run", "--lang", "en"])
    assert out.exit_code == 0, out.output
    out = runner.invoke(app, ["score-stream", "latest"])
    assert out.exit_code == 1  # no streaming runs yet
    out = runner.invoke(app, ["run-stream", "--providers", "nope", "--dry-run"])
    assert out.exit_code != 0


async def test_dry_run_prices_providers_missing_keys(tmp_settings, tmp_path, monkeypatch):
    from rich.console import Console

    for k in ("DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY",
              "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):  # fmt: skip
        monkeypatch.delenv(k, raising=False)
    clip = make_clip(tmp_path, "a").model_copy(update={"duration_s": 60.0})
    console = Console(record=True, width=200)
    res = await run_stream(["deepgram-stream", "gemini-stream"], "verified", clips=[clip], settings=tmp_settings,
                           dry_run=True, console=console)  # fmt: skip
    assert set(res.skipped_providers) == {"deepgram-stream", "gemini-stream"}
    out = console.export_text()
    assert "if you add the missing keys" in out
    assert f"${0.0077 + 0.009:.4f}" in out
