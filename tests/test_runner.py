from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest
from rich.console import Console
from typer.testing import CliRunner

from clipbench.models import Clip, Result
from clipbench.providers.base import ProviderSkip
from clipbench.providers.fake import FakeProvider
from clipbench.runner import REF_COLUMNS, ROW_COLUMNS, TEXT_COLUMNS, run
from tests.fixtures.make_clips import make_clips


@pytest.fixture
def clips(tmp_path):
    return make_clips(tmp_path / "audio")


class Unavailable(FakeProvider):
    id: ClassVar[str] = "fake-off"

    def availability(self):
        return False, "fake-off: skipped, missing FAKE_KEY in .env"


class SkipsOne(FakeProvider):
    id: ClassVar[str] = "fake-skip"

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is not None and clip.video_id == "fixture0003":
            raise ProviderSkip("no output for this clip")
        return await super().transcribe(audio_path, language, clip=clip)


class Flaky(FakeProvider):
    """Fails on chosen videos until `broken` is cleared."""

    id: ClassVar[str] = "fake-flaky"

    def __init__(self, broken: set[str], **kw):
        super().__init__(**kw)
        self.broken = broken

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is not None and clip.video_id in self.broken:
            self.calls += 1
            raise RuntimeError("boom")
        return await super().transcribe(audio_path, language, clip=clip)


class Slow(FakeProvider):
    id: ClassVar[str] = "fake-slow"
    timeout_s: ClassVar[float] = 0.05

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        await asyncio.sleep(1)
        return await super().transcribe(audio_path, language, clip=clip)


async def test_end_to_end_writes_all_files(tmp_settings, clips):
    res = await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-e2e")
    assert res.rows_path == tmp_settings.runs_dir / "r-e2e" / "rows.parquet"
    rows = pd.read_parquet(res.rows_path)
    assert list(rows.columns) == ROW_COLUMNS
    assert len(rows) == 5
    assert (rows["status"] == "ok").all()
    assert set(rows["repeat"]) == {0}
    assert rows["tier"].unique().tolist() == ["verified"]
    assert rows["language"].unique().tolist() == ["ja"]
    assert (rows["hyp_chars"] > 0).all()
    assert json.loads(rows.set_index("video_id").loc["fixture0005", "tags"]) == ["noisy", "background_music"]
    assert not rows["provider_version"].isna().any()

    texts = pd.read_parquet(tmp_settings.runs_text_dir / "r-e2e" / "texts.parquet")
    assert list(texts.columns) == TEXT_COLUMNS
    assert len(texts) == 5
    refs = pd.read_parquet(tmp_settings.runs_text_dir / "r-e2e" / "refs.parquet")
    assert list(refs.columns) == REF_COLUMNS
    assert set(refs["video_id"]) == {c.video_id for c in clips}
    assert refs.set_index("video_id").loc["fixture0001", "ref_text"] == "今日はいい天気ですね。"

    # No caption text in the published results folder.
    assert "hyp_text" not in rows.columns
    assert not (tmp_settings.runs_dir / "r-e2e" / "rows.part.jsonl").exists()

    meta = json.loads(res.meta_path.read_text())
    for k in ("run_id", "created_at", "git_commit", "git_dirty", "python", "packages", "providers",
              "sample_seed", "tier", "language", "n_clips", "clipbench_version"):
        assert k in meta
    assert meta["n_clips"] == 5 and meta["tier"] == "verified" and meta["language"] == "ja"
    assert meta["providers"][0]["id"] == "fake" and meta["providers"][0]["model_id"] == "fake-1"
    assert "pricing_checked_on" in meta["providers"][0]
    assert "pandas" in meta["packages"]


async def test_default_run_id_format(tmp_settings, clips):
    res = await run([FakeProvider()], "verified", clips=clips[:1], settings=tmp_settings, progress=False)
    import re

    assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-f]{4}", res.run_id)


async def test_limit(tmp_settings, clips):
    res = await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, limit=2)
    assert len(res.rows) == 2


async def test_unavailable_provider_skipped_with_message(tmp_settings, clips):
    p = Unavailable()
    res = await run([p, FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False)
    assert res.skipped_providers == {"fake-off": "fake-off: skipped, missing FAKE_KEY in .env"}
    assert p.calls == 0
    assert set(res.rows["provider"]) == {"fake"}
    meta = json.loads(res.meta_path.read_text())
    assert "fake-off" in meta["skipped_providers"]


async def test_unknown_id_and_missing_adapter(tmp_settings, clips):
    with pytest.raises(KeyError):
        await run(["nope"], "verified", clips=clips, settings=tmp_settings, progress=False)


async def test_provider_skip_is_status_skipped(tmp_settings, clips):
    res = await run([SkipsOne()], "verified", clips=clips, settings=tmp_settings, progress=False)
    row = res.rows.set_index("video_id").loc["fixture0003"]
    assert row["status"] == "skipped" and row["error"] == "no output for this clip"
    assert (res.rows["status"] == "ok").sum() == 4
    texts = pd.read_parquet(res.texts_path)
    assert "fixture0003" not in set(texts["video_id"])


async def test_errors_and_timeouts_do_not_crash(tmp_settings, clips):
    res = await run(
        [Flaky({"fixture0002"}), Slow()], "verified", clips=clips, settings=tmp_settings, progress=False
    )
    flaky = res.rows[res.rows["provider"] == "fake-flaky"].set_index("video_id")
    assert flaky.loc["fixture0002", "status"] == "error"
    assert "RuntimeError: boom" in flaky.loc["fixture0002", "error"]
    slow = res.rows[res.rows["provider"] == "fake-slow"]
    assert (slow["status"] == "error").all()
    assert slow["error"].str.startswith("timeout").all()


async def test_resume_retries_errors_only(tmp_settings, clips):
    p = Flaky({"fixture0002", "fixture0004"})
    await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-res", no_cache=True)
    assert p.calls == 5
    p2 = Flaky(set())
    res = await run(
        [p2], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-res", no_cache=True
    )
    assert p2.calls == 2  # only the two errors were retried
    assert (res.rows["status"] == "ok").all()
    assert len(res.rows) == 5
    texts = pd.read_parquet(res.texts_path)
    assert len(texts) == 5


async def test_resume_from_part_files_after_crash(tmp_settings, clips):
    await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-crash")
    rows_dir = tmp_settings.runs_dir / "r-crash"
    text_dir = tmp_settings.runs_text_dir / "r-crash"
    rows = pd.read_parquet(rows_dir / "rows.parquet")
    texts = pd.read_parquet(text_dir / "texts.parquet")
    # Simulate a crash after two rows: only part files exist, with a torn last line.
    (rows_dir / "rows.parquet").unlink()
    (text_dir / "texts.parquet").unlink()
    keep = rows.head(2).astype(object).where(rows.head(2).notna(), None)
    with (rows_dir / "rows.part.jsonl").open("w") as fh:
        for r in keep.to_dict("records"):
            fh.write(json.dumps(r, default=str) + "\n")
        fh.write('{"run_id": "r-cr')
    with (text_dir / "texts.part.jsonl").open("w") as fh:
        for t in texts.head(2).astype(object).to_dict("records"):
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    p = FakeProvider()
    res = await run(
        [p], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-crash", no_cache=True
    )
    assert p.calls == 3
    assert len(res.rows) == 5 and (res.rows["status"] == "ok").all()
    assert len(pd.read_parquet(res.texts_path)) == 5
    assert not (rows_dir / "rows.part.jsonl").exists()


async def test_dry_run_prints_plan_and_writes_nothing(tmp_settings, clips):
    console = Console(record=True, width=200)
    p = FakeProvider(cost_per_minute=0.6)
    res = await run(
        [p, Unavailable()], "verified", clips=clips, settings=tmp_settings, console=console, dry_run=True
    )
    out = console.export_text()
    assert res.dry_run and res.allowed
    assert p.calls == 0
    for s in ("fake", "fake-off", "missing FAKE_KEY", "total audio minutes", "total estimated cost",
              "spent so far", "cap: $10.00", "a real run would be allowed"):
        assert s in out
    assert f"${sum(c.duration_s for c in clips) * 0.01:.4f}" in out
    assert not tmp_settings.runs_dir.exists()


async def test_dry_run_reports_refusal(tmp_settings, clips):
    console = Console(record=True, width=200)

    class PaidFake(FakeProvider):
        paid: ClassVar[bool] = True

    res = await run(
        [PaidFake(cost_per_minute=600.0)], "verified", clips=clips, settings=tmp_settings, console=console,
        dry_run=True,
    )
    assert not res.allowed
    assert "REFUSED" in console.export_text()


# ---------------------------------------------------------------- CLI


@pytest.fixture
def fake_references(monkeypatch, clips):
    mod = types.ModuleType("clipbench.data.references")
    mod.load_clips = lambda tier, settings=None, limit=None: list(clips)
    monkeypatch.setitem(sys.modules, "clipbench.data.references", mod)
    return mod


def _app():
    import typer

    from clipbench import runner_cli

    app = typer.Typer()

    @app.callback()
    def _root() -> None:
        pass

    runner_cli.register(app)
    return app


def test_cli_dry_run(tmp_settings, fake_references):
    r = CliRunner().invoke(_app(), ["run", "--providers", "fake", "--tier", "verified", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert not tmp_settings.runs_dir.exists()


def test_cli_real_run_and_budget_refusal(tmp_settings, fake_references, monkeypatch):
    r = CliRunner().invoke(_app(), ["run", "--providers", "fake", "--run-id", "cli-1", "--limit", "3"])
    assert r.exit_code == 0, r.output
    assert len(pd.read_parquet(tmp_settings.runs_dir / "cli-1" / "rows.parquet")) == 3

    monkeypatch.setattr(FakeProvider, "paid", True)
    monkeypatch.setattr(FakeProvider, "price_per_minute", lambda self: 1000.0)
    r = CliRunner().invoke(_app(), ["run", "--providers", "fake", "--run-id", "cli-2", "--no-cache"])
    assert r.exit_code == 2
    assert "refusing to start" in r.output
    assert not (tmp_settings.runs_dir / "cli-2").exists()


# ---------------------------------------------------------------- free adapters


async def test_youtube_auto_reads_manifest_track(tmp_settings, clips):
    from clipbench.providers.youtube_auto import YouTubeAuto

    track = tmp_settings.data_dir / "tracks" / "fixture0001.ja.json3"
    track.parent.mkdir(parents=True, exist_ok=True)
    track.write_text(
        json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "[音楽]"}]},
                    {"tStartMs": 1500, "dDurationMs": 1500, "segs": [{"utf8": "今日は"}, {"utf8": "いい天気", "tOffsetMs": 400}]},
                ]
            },
            ensure_ascii=False,
        )
    )
    manifest = pd.DataFrame(
        [
            {"video_id": "fixture0001", "has_auto_track": True, "auto_track_key": "ja", "auto_track_path": str(track)},
            {"video_id": "fixture0002", "has_auto_track": False, "auto_track_key": None, "auto_track_path": None},
        ]
    )
    tmp_settings.data_dir.mkdir(parents=True, exist_ok=True)
    manifest.to_parquet(tmp_settings.manifest_path, index=False)
    p = YouTubeAuto()
    assert p.availability()[0]
    assert not p.paid and not p.uploads_audio and p.estimate_cost(600) == 0
    res = await run([p], "verified", clips=clips[:2], settings=tmp_settings, progress=False)
    rows = res.rows.set_index("video_id")
    assert rows.loc["fixture0001", "status"] == "ok"
    assert rows.loc["fixture0001", "cost_usd"] == 0
    assert rows.loc["fixture0002", "status"] == "skipped"
    texts = pd.read_parquet(res.texts_path).set_index("video_id")
    assert "音楽" not in texts.loc["fixture0001", "hyp_text"]
    assert "今日は" in texts.loc["fixture0001", "hyp_text"]


def test_youtube_auto_unavailable_without_manifest(tmp_settings):
    from clipbench.providers.youtube_auto import YouTubeAuto

    ok, msg = YouTubeAuto().availability()
    assert not ok and "manifest" in msg


async def test_wayin_file_route_and_skip(tmp_settings, clips, monkeypatch):
    from clipbench.providers.wayin import Wayin

    monkeypatch.delenv("CLIPBENCH_WAYIN_DB", raising=False)
    ok, msg = Wayin().availability()
    assert not ok and "Transcript.source='wayin'" in msg

    d = tmp_settings.data_dir / "wayin"
    d.mkdir(parents=True)
    (d / "fixture0001.json").write_text(
        json.dumps({"cues": [{"startMs": 0, "endMs": 900, "text": "今日は"}, {"startMs": 900, "endMs": 2000, "text": "いい天気"}]})
    )
    p = Wayin()
    assert p.availability()[0]
    res = await run([p], "verified", clips=clips[:2], settings=tmp_settings, progress=False)
    rows = res.rows.set_index("video_id")
    assert rows.loc["fixture0001", "status"] == "ok"
    assert rows.loc["fixture0002", "status"] == "skipped"
    texts = pd.read_parquet(res.texts_path).set_index("video_id")
    assert texts.loc["fixture0001", "hyp_text"] == "今日はいい天気"


def test_whisper_local_contract_without_weights():
    from clipbench.providers.whisper_local import WhisperLocal, WhisperLocalTurbo

    a, b = WhisperLocal(), WhisperLocalTurbo()
    assert a.id == "whisper-local" and b.id == "whisper-local-turbo"
    assert a.model_id in ("mlx-community/whisper-large-v3-mlx", "large-v3")
    assert b.model_id in ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo")
    assert not a.paid and not a.uploads_audio and a.concurrency == 1
    assert a.estimate_cost(3600) == 0
    s = a.settings()
    assert s["temperature"] == 0.0 and s["condition_on_previous_text"] is False and s["word_timestamps"] is True
    assert a.settings_hash() != b.settings_hash()
