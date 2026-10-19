from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest

from clipbench.models import Clip, Result
from clipbench.providers.fake import FakeProvider
from clipbench.runner import run
from clipbench.scoring import reliability as rel
from tests.fixtures.make_clips import make_clips


def _row(provider, status="ok", latency=100.0, retries=0, http=None, timed_out=False, cached=False, run_id="r1",
         vid="v1"):  # fmt: skip
    return {"run_id": run_id, "provider": provider, "model_id": "m", "video_id": vid, "tier": "verified",
            "language": "ja", "status": status, "latency_ms": latency, "retries": retries, "http_status": http,
            "timed_out": timed_out, "cached": cached}  # fmt: skip


def test_rates_and_latency_tail():
    rows = pd.DataFrame(
        [_row("a", latency=float(i), vid=f"v{i}") for i in range(1, 101)]
        + [_row("a", status="error", http=429, retries=4, vid="x1"),
           _row("a", status="error", timed_out=True, latency=120000.0, vid="x2"),
           _row("a", cached=True, latency=5.0, vid="c1"),  # no request went out
           _row("a", status="skipped", vid="s1"),
           _row("b", vid="v1")]
    )  # fmt: skip
    out = rel.reliability(rows).set_index("provider")
    a = out.loc["a"]
    assert a["n_requests"] == 102 and a["n_errors"] == 2 and a["n_timeouts"] == 1
    assert a["failure_rate"] == pytest.approx(2 / 102)
    assert a["failure_ci_low"] < a["failure_rate"] < a["failure_ci_high"]
    assert a["timeout_rate"] == pytest.approx(1 / 102)
    assert a["rate_limited_share"] == pytest.approx(1 / 102)
    assert a["mean_retries"] == pytest.approx(4 / 102) and a["max_retries"] == 4
    assert a["latency_p50"] == pytest.approx(50.5)
    assert a["latency_p99"] == pytest.approx(99.01)
    assert out.loc["b", "failure_rate"] == 0.0
    assert list(out.reset_index().columns) == rel.RELIABILITY_COLUMNS


def test_wilson_bounds():
    lo, hi = rel.wilson(0, 50)
    assert lo == 0.0 and 0 < hi < 0.1
    assert all(map(pd.isna, rel.wilson(0, 0)))


class Flaky(FakeProvider):
    id: ClassVar[str] = "fake-flaky"

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is not None and clip.video_id == "fixture0002":
            exc = RuntimeError("rate limited")
            exc.http_status = 429  # type: ignore[attr-defined]
            exc.retries = 3  # type: ignore[attr-defined]
            raise exc
        return await super().transcribe(audio_path, language, clip=clip)


class Slow(FakeProvider):
    id: ClassVar[str] = "fake-slow"
    timeout_s: ClassVar[float] = 0.05

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is not None and clip.video_id == "fixture0001":
            await asyncio.sleep(1)
        return await super().transcribe(audio_path, language, clip=clip)


async def test_runner_rows_feed_reliability_files_and_export(tmp_settings, tmp_path):
    clips = make_clips(tmp_path / "audio")
    await run([Flaky(), Slow()], "verified", clips=clips, settings=tmp_settings, progress=False, run_id="r-rel")
    path = rel.write_run_reliability("r-rel", tmp_settings)
    df = pd.read_parquet(path).set_index("provider")
    assert df.loc["fake-flaky", "n_rate_limited"] == 1 and df.loc["fake-flaky", "mean_retries"] == 0.6
    assert df.loc["fake-slow", "n_timeouts"] == 1 and df.loc["fake-slow", "failure_rate"] == 0.2

    # A canary run counts towards the history across runs too.
    canary_runs = tmp_settings.results_dir / "canary" / "runs" / "canary-ja-20261001-0317"
    canary_runs.mkdir(parents=True)
    pd.read_parquet(tmp_settings.runs_dir / "r-rel" / "rows.parquet").assign(run_id="canary-ja-20261001-0317").to_parquet(
        canary_runs / "rows.parquet", index=False
    )
    hist = pd.read_parquet(rel.write_history(tmp_settings)).set_index("provider")
    assert hist.loc["fake-flaky", "n_runs"] == 2 and hist.loc["fake-flaky", "n_requests"] == 10

    sec = rel.export_section("r-rel", tmp_settings)["reliability"]
    assert {r["provider"] for r in sec["run"]} == {"fake-flaky", "fake-slow"}
    assert {r["n_runs"] for r in sec["all_runs"]} == {2}
    json.dumps(sec, allow_nan=False)
    assert (tmp_settings.results_dir / "r-rel" / "reliability.parquet").exists()


def test_export_section_without_runs(tmp_settings):
    assert rel.export_section("nope", tmp_settings) is None
