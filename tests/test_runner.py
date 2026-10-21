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
    for s in ("fake", "fake-off", "missing FAKE_KEY", "audio minutes: ", "per provider", "total estimated cost",
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


# ---------------------------------------------------------------- languages and crash safety


def _with_language(clips, language: str, prefix: str):
    out = []
    for c in clips:
        ref = c.reference.model_copy(update={"video_id": prefix + c.video_id, "language": language})
        out.append(c.model_copy(update={"video_id": prefix + c.video_id, "language": language, "reference": ref}))
    return out


async def test_mixed_language_run_is_refused(tmp_settings, clips):
    mixed = list(clips) + _with_language(clips, "en", "en-")
    with pytest.raises(ValueError, match="one language"):
        await run([FakeProvider()], "verified", clips=mixed, settings=tmp_settings, progress=False, run_id="r-mix")
    assert not (tmp_settings.runs_dir / "r-mix").exists()


async def test_language_filter_picks_one_language(tmp_settings, clips):
    mixed = list(clips) + _with_language(clips, "en", "en-")
    res = await run(
        [FakeProvider()], "verified", clips=mixed, language="en", settings=tmp_settings, progress=False, run_id="r-en"
    )
    assert set(res.rows["language"]) == {"en"} and len(res.rows) == 5
    assert json.loads(res.meta_path.read_text())["language"] == "en"


def test_cli_lang_option(tmp_settings, monkeypatch, clips):
    mixed = list(clips) + _with_language(clips, "en", "en-")
    mod = types.ModuleType("clipbench.data.references")
    mod.load_clips = lambda tier, settings=None, limit=None: list(mixed)
    monkeypatch.setitem(sys.modules, "clipbench.data.references", mod)
    r = CliRunner().invoke(_app(), ["run", "--providers", "fake", "--run-id", "cli-mix"])
    assert r.exit_code == 2 and "--lang" in r.output
    r = CliRunner().invoke(_app(), ["run", "--providers", "fake", "--run-id", "cli-ja", "--lang", "ja"])
    assert r.exit_code == 0, r.output
    assert set(pd.read_parquet(tmp_settings.runs_dir / "cli-ja" / "rows.parquet")["language"]) == {"ja"}


async def test_resume_does_not_keep_ok_row_without_text(tmp_settings, clips):
    """A crash can leave an ok row in rows.part.jsonl whose text line never made it (or was torn):
    the row must be redone, not scored as an empty hypothesis."""
    await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-torn")
    rows_dir = tmp_settings.runs_dir / "r-torn"
    text_dir = tmp_settings.runs_text_dir / "r-torn"
    rows = pd.read_parquet(rows_dir / "rows.parquet")
    texts = pd.read_parquet(text_dir / "texts.parquet")
    (rows_dir / "rows.parquet").unlink()
    (text_dir / "texts.parquet").unlink()
    with (rows_dir / "rows.part.jsonl").open("w") as fh:
        for r in rows.head(3).astype(object).where(rows.head(3).notna(), None).to_dict("records"):
            fh.write(json.dumps(r, default=str) + "\n")
    with (text_dir / "texts.part.jsonl").open("w") as fh:
        for t in texts.head(2).astype(object).to_dict("records"):
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
        fh.write('{"provider": "fake", "video_')  # torn: the third row's text is lost
    p = FakeProvider()
    res = await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-torn", no_cache=True)
    assert p.calls == 3
    out = pd.read_parquet(res.texts_path)
    assert len(out) == 5 and set(out["video_id"]) == set(rows["video_id"])


async def test_run_without_clips_is_refused(tmp_settings):
    """No clips (wrong --lang or tier, nothing verified yet) must not write an empty run that then
    becomes `latest` for score, report and the site."""
    with pytest.raises(ValueError, match="no clips"):
        await run([FakeProvider()], "verified", clips=[], settings=tmp_settings, progress=False, run_id="r-none")
    assert not (tmp_settings.runs_dir / "r-none").exists()
    console = Console(record=True, width=250)
    res = await run([FakeProvider()], "verified", clips=[], settings=tmp_settings, progress=False, dry_run=True,
                    console=console)  # fmt: skip
    assert res.dry_run
    # a zero clip plan must not read like a working setup
    out = console.export_text()
    assert "no clips to run (tier verified)" in out and "--tier creator" in out


def test_cli_dry_run_rejects_unknown_language_and_empty_plan(tmp_settings):
    from typer.testing import CliRunner

    from clipbench.cli import app

    r = CliRunner().invoke(app, ["run", "--dry-run", "--lang", "xx", "--providers", "fake"])
    assert r.exit_code == 2 and "--lang must be one of" in r.output
    r = CliRunner().invoke(app, ["run", "--dry-run", "--providers", "fake"])
    assert r.exit_code == 1


async def test_paid_provider_without_price_is_skipped(tmp_settings, clips):
    """A paid adapter with no price in pricing.toml would be estimated and reserved at $0, so the
    budget guard could never stop it."""

    class PaidNoPrice(FakeProvider):
        id: ClassVar[str] = "fake-paid-free"
        paid: ClassVar[bool] = True

    p = PaidNoPrice(cost_per_minute=0.0)
    res = await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, dry_run=True)
    assert "fake-paid-free" in res.skipped_providers and "pricing.toml" in res.skipped_providers["fake-paid-free"]


async def test_runner_timeout_is_the_whole_job(tmp_settings, clips):
    """timeout_s bounds one HTTP request; the runner must allow the whole job (retries, polling), or a
    queued AssemblyAI job is cancelled at 120 s, counted as spent and errored while it still bills."""
    from clipbench.providers.assemblyai import AssemblyAI
    from clipbench.providers.deepgram import Deepgram

    aai = AssemblyAI()
    assert aai.job_timeout_s() >= aai.poll_timeout_s + aai.timeout_s
    dg = Deepgram()
    assert dg.job_timeout_s() >= dg.timeout_s * dg.retry.max_attempts
    from clipbench.providers.aws_transcribe import AwsTranscribe

    assert AwsTranscribe(s3_client=object(), transcribe_client=object()).job_timeout_s() > AwsTranscribe.timeout_s

    class SlowJob(FakeProvider):
        id: ClassVar[str] = "fake-slowjob"
        timeout_s: ClassVar[float] = 0.05

        def job_timeout_s(self) -> float:
            return 5.0

        async def transcribe(self, audio_path, language, *, clip=None):
            await asyncio.sleep(0.2)
            return await super().transcribe(audio_path, language, clip=clip)

    res = await run([SlowJob()], "verified", clips=clips[:1], settings=tmp_settings, progress=False, run_id="r-job")
    assert (res.rows["status"] == "ok").all()


async def test_published_rows_do_not_carry_local_paths(tmp_settings, clips):
    """rows.parquet and run_meta.json are published: skip and error messages that name local files
    (wayin export dir, a missing auto track) must not leak the owner's home directory."""

    class PathSkip(FakeProvider):
        id: ClassVar[str] = "fake-pathskip"

        async def transcribe(self, audio_path, language, *, clip=None):
            raise ProviderSkip(f"auto track missing on disk: {tmp_settings.data_dir / 'tracks' / 'x.json3'}")

    class PathOff(FakeProvider):
        id: ClassVar[str] = "fake-pathoff"

        def availability(self):
            return False, f"fake-pathoff: skipped, put exports in {Path.home() / 'secret' / 'wayin'}"

    res = await run(
        [PathSkip(), PathOff()], "verified", clips=clips[:1], settings=tmp_settings, progress=False, run_id="r-path"
    )
    err = res.rows["error"].iloc[0]
    assert str(tmp_settings.data_dir) not in err and "data/tracks/x.json3" in err
    meta = res.meta_path.read_text()
    assert str(Path.home()) not in meta and "~/secret/wayin" in meta


async def test_resume_with_fewer_clips_keeps_references(tmp_settings, clips):
    """Resuming a run with --limit (say to retry a few errors) must not drop the reference snapshot of
    the clips it did not touch, or scoring silently loses their rows."""
    await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-refs")
    res = await run(
        [FakeProvider()], "verified", clips=clips, limit=2, settings=tmp_settings, progress=False, run_id="r-refs"
    )
    refs = pd.read_parquet(res.refs_path)
    assert sorted(refs["video_id"]) == sorted(c.video_id for c in clips)
    assert len(res.rows) == 5
    assert json.loads(res.meta_path.read_text())["n_clips"] == 5


async def test_resume_with_fewer_providers_keeps_their_meta(tmp_settings, clips):
    await run([FakeProvider(), SkipsOne()], "verified", clips=clips, settings=tmp_settings, progress=False,
              run_id="r-meta")  # fmt: skip
    res = await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-meta")
    ids = [p["id"] for p in json.loads(res.meta_path.read_text())["providers"]]
    assert sorted(ids) == ["fake", "fake-skip"]


def test_cli_pipeline_accepts_latest(tmp_settings, fake_references, monkeypatch, tmp_path):
    """README and the Makefile run score, errors, report and export with `latest`."""
    from clipbench.cli import app

    monkeypatch.setenv("CLIPBENCH_WEB_DIR", str(tmp_path / "web"))
    r = CliRunner().invoke(app, ["run", "--providers", "fake", "--run-id", "cli-latest"])
    assert r.exit_code == 0, r.output
    for cmd in ("score", "errors", "report", "export"):
        r = CliRunner().invoke(app, [cmd, "latest"])
        assert r.exit_code == 0, f"{cmd}: {r.output}"
    assert (tmp_settings.results_dir / "cli-latest" / "errors.json").exists()
    assert (tmp_settings.results_dir / "cli-latest" / "report.md").exists()
    assert not (tmp_settings.results_dir / "latest").exists()


async def test_run_options_reach_providers_and_change_cache_key(tmp_settings, clips):
    base = FakeProvider()
    plain_hash = base.settings_hash()
    p = FakeProvider()
    res = await run(
        [p], "verified", clips=clips[:2], settings=tmp_settings, progress=False, run_id="r-opts",
        options={"diarize": True, "detect_language": False, "keyterms": ["clipbench"]},
    )
    assert p.options["diarize"] is True and p.options["keyterms"] == ["clipbench"]
    assert "detect_language" not in p.options  # falsy options are not sent
    assert p.settings_hash() != plain_hash
    rows = pd.read_parquet(res.rows_path)
    for col in ("retries", "timed_out", "http_status", "detected_language", "options"):
        assert col in rows.columns
    assert json.loads(rows["options"].iloc[0]) == {"diarize": True, "keyterms": ["clipbench"]}
    assert (rows["retries"] == 0).all() and not rows["timed_out"].any()


async def test_providers_run_one_at_a_time_by_default(tmp_settings, clips):
    log: list[tuple[str, str]] = []

    class Logged(FakeProvider):
        async def transcribe(self, audio_path, language, *, clip=None):
            log.append((self.id, "start"))
            await asyncio.sleep(0.01)
            out = await super().transcribe(audio_path, language, clip=clip)
            log.append((self.id, "end"))
            return out

    class A(Logged):
        id = "fake-a"

    class B(Logged):
        id = "fake-b"

    await run([A(), B()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-seq")
    first_b = next(i for i, (pid, _) in enumerate(log) if pid == "fake-b")
    assert all(pid == "fake-a" for pid, _ in log[:first_b])  # every A call finished before B began
    assert sum(1 for pid, ev in log if pid == "fake-a" and ev == "end") == len(clips)
    meta = json.loads((tmp_settings.runs_dir / "r-seq" / "run_meta.json").read_text())
    assert meta["latency_mode"]["sequential_providers"] is True and meta["latency_mode"]["max_concurrency"] == 4


class QuotaAfterOne(FakeProvider):
    id: ClassVar[str] = "fake-quota"

    async def transcribe(self, audio_path, language, *, clip=None):
        from clipbench.providers.base import ProviderQuotaExhausted

        if self.calls >= 1:
            raise ProviderQuotaExhausted("429 quota exceeded")
        return await super().transcribe(audio_path, language, clip=clip)


async def test_quota_stop_is_reported_and_dry_run_points_to_resume(tmp_settings, clips):
    """A provider that runs out of account quota: its other clips are 'skipped: quota' (not errors), the
    run meta lists it, the CLI prints a resume command, and the next dry run names the run to resume."""
    from clipbench.runner_cli import resume_hint

    res = await run([QuotaAfterOne()], "verified", clips=clips, settings=tmp_settings, progress=False,
                    stability=False)  # fmt: skip
    rows = res.rows
    assert (rows["status"] == "ok").sum() == 1
    assert set(rows.loc[rows["status"] != "ok", "error"]) == {"quota"}
    assert set(rows.loc[rows["status"] != "ok", "status"]) == {"skipped"}
    meta = json.loads(res.meta_path.read_text())
    assert meta["quota_stopped"] == ["fake-quota"]
    hint = resume_hint(res, "verified", "ja")
    assert hint and "account out of quota" in hint
    assert f"clipbench run --run-id {res.run_id} --tier verified --lang ja --providers fake-quota" in hint

    console = Console(record=True, width=250)
    await run([QuotaAfterOne()], "verified", clips=clips, settings=tmp_settings, console=console, dry_run=True)
    out = console.export_text()
    assert f"stopped by account quota in {res.run_id}" in out
    assert f"resume with --run-id {res.run_id}" in out


async def test_dry_run_total_leaves_out_refused_providers(tmp_settings, clips, monkeypatch):
    """A real run drops providers the verification gate refuses, so the dry run's total must not count them."""
    from clipbench.providers import verification

    class PaidFake(FakeProvider):
        paid: ClassVar[bool] = True

    monkeypatch.setattr(verification, "check", lambda ps, allow: ({"fake": "price_status is unverified"}, {}))
    console = Console(record=True, width=250)
    res = await run([PaidFake(cost_per_minute=0.6)], "verified", clips=clips, settings=tmp_settings,
                    console=console, dry_run=True)  # fmt: skip
    out = console.export_text()
    assert "total estimated cost: $0.0000" in out
    assert "for refused providers, only if named in --allow-unverified" in out
    assert res.allowed


async def test_provider_without_the_language_is_skipped_not_priced(tmp_settings, clips):
    class EnglishOnly(FakeProvider):
        id: ClassVar[str] = "fake-en"
        paid: ClassVar[bool] = True
        languages: ClassVar[frozenset[str]] = frozenset({"en"})

    console = Console(record=True, width=250)
    res = await run([EnglishOnly(cost_per_minute=0.6), FakeProvider()], "verified", clips=clips,
                    settings=tmp_settings, console=console, dry_run=True)  # fmt: skip
    assert "fake-en" not in res.estimate.providers
    assert "does not support language ja" in res.skipped_providers["fake-en"]
