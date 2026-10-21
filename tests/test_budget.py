from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import ClassVar

import pytest

from clipbench.budget import (
    BudgetExceeded,
    BudgetGuard,
    check_can_start,
    estimate,
    load_spend,
    stability_subset,
)
from clipbench.models import Clip, Result
from clipbench.providers.fake import FakeProvider
from clipbench.runner import run
from tests.fixtures.make_clips import make_clips


class PaidFake(FakeProvider):
    id: ClassVar[str] = "fake"
    paid: ClassVar[bool] = True


class Overbilling(PaidFake):
    """Bills 10x its own estimate, so a run that passes the start check crosses the cap mid run."""

    concurrency: ClassVar[int] = 1

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        res = await super().transcribe(audio_path, language, clip=clip)
        return res.model_copy(update={"cost_usd": res.cost_usd * 10})


@pytest.fixture
def clips(tmp_path):
    return make_clips(tmp_path / "audio")


def test_estimate_includes_stability_reruns(clips):
    p = PaidFake(cost_per_minute=60.0)  # $1 per second
    est = estimate([p], clips, stability_n=3, stability_repeats=3, seed=7)
    e = est.providers["fake"]
    total_seconds = sum(c.duration_s for c in clips)
    subset_seconds = sum(c.duration_s for c in stability_subset(clips, 3, 7))
    assert e.main_usd == pytest.approx(total_seconds)
    assert e.stability_usd == pytest.approx(3 * subset_seconds)
    assert est.total_usd == pytest.approx(total_seconds + 3 * subset_seconds)
    assert est.total_audio_seconds == pytest.approx(total_seconds)


def test_free_provider_has_no_stability_cost(clips):
    est = estimate([FakeProvider()], clips, stability_n=20, stability_repeats=3)
    assert est.total_usd == 0
    assert est.providers["fake"].stability_repeats == 0


def test_stability_subset_is_seeded_and_order_free(clips):
    a = stability_subset(clips, 3, 7)
    b = stability_subset(list(reversed(clips)), 3, 7)
    assert [c.video_id for c in a] == [c.video_id for c in b]
    assert len(stability_subset(clips, 20, 7)) == len(clips)


def test_check_can_start(tmp_path):
    check_can_start(4.0, 10.0, spent=6.0)
    with pytest.raises(BudgetExceeded, match="cap"):
        check_can_start(4.01, 10.0, spent=6.0)
    path = tmp_path / "spend.json"
    path.write_text(json.dumps({"total_usd": 9.5, "by_provider": {}, "events": []}))
    with pytest.raises(BudgetExceeded):
        check_can_start(1.0, 10.0, path=path)


async def test_guard_reserve_record_and_atomic_file(tmp_path):
    path = tmp_path / "data" / "spend.json"
    g = BudgetGuard(path, cap=1.0, run_id="r1")
    assert await g.reserve(0.6)
    assert not await g.reserve(0.5)  # 0.6 reserved + 0.5 > 1.0
    await g.record("p", "v1", 0.55, reserved_usd=0.6)
    assert g.reserved == 0
    data = load_spend(path)
    assert data["total_usd"] == pytest.approx(0.55)
    assert data["by_provider"]["p"] == pytest.approx(0.55)
    assert data["events"][0]["run_id"] == "r1" and data["events"][0]["video_id"] == "v1"
    assert not list(path.parent.glob("*.tmp"))


async def test_guard_is_safe_under_concurrency(tmp_path):
    g = BudgetGuard(tmp_path / "spend.json", cap=1.0, run_id="r")

    async def one(i):
        if await g.reserve(0.1):
            await asyncio.sleep(0)
            await g.record("p", f"v{i}", 0.1, 0.1)
            return True
        return False

    results = await asyncio.gather(*(one(i) for i in range(30)))
    assert sum(results) == 10
    assert load_spend(tmp_path / "spend.json")["total_usd"] == pytest.approx(1.0)


async def test_runner_refuses_to_start_over_cap(tmp_settings, clips, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_BUDGET_USD", "1.0")
    from clipbench.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    p = PaidFake(cost_per_minute=60.0)
    with pytest.raises(BudgetExceeded, match="refusing to start"):
        await run([p], "verified", clips=clips, settings=settings, progress=False)
    assert p.calls == 0
    assert not settings.spend_path.exists()
    assert not (settings.runs_dir).exists()


async def test_runner_refuses_when_already_spent(tmp_settings, clips):
    tmp_settings.spend_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_settings.spend_path.write_text(json.dumps({"total_usd": tmp_settings.budget_usd, "by_provider": {}, "events": []}))
    p = PaidFake(cost_per_minute=0.6)
    with pytest.raises(BudgetExceeded):
        await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, stability=False)
    assert p.calls == 0


async def test_mid_run_stop_marks_rest_budget_and_updates_spend(tmp_settings, clips, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_BUDGET_USD", "10.0")
    from clipbench.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    # $0.10 per second estimated (total $2.05, under the cap); actually billed $1 per second.
    p = Overbilling(cost_per_minute=6.0)
    res = await run([p], "verified", clips=clips, settings=settings, progress=False, stability=False)
    rows = res.rows.set_index("video_id")
    # Sorted by id: 3 s ($3), 4.5 s ($4.5), 2 s ($2) -> $9.5 spent; the 6 s clip would reserve $0.6 -> refused.
    assert list(rows.loc[["fixture0001", "fixture0002", "fixture0003"], "status"]) == ["ok"] * 3
    assert list(rows.loc[["fixture0004", "fixture0005"], "status"]) == ["skipped"] * 2
    assert list(rows.loc[["fixture0004", "fixture0005"], "error"]) == ["budget"] * 2
    assert p.calls == 3
    spend = load_spend(settings.spend_path)
    assert spend["total_usd"] == pytest.approx(9.5)
    assert spend["by_provider"]["fake"] == pytest.approx(9.5)
    assert len(spend["events"]) == 3
    assert {e["run_id"] for e in spend["events"]} == {res.run_id}
    meta = json.loads(res.meta_path.read_text())
    assert meta["budget_stopped"] == ["fake"]


async def test_spend_updated_and_cached_rerun_costs_nothing(tmp_settings, clips):
    p = PaidFake(cost_per_minute=0.6)  # $0.01 per second
    await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, stability=False)
    first = load_spend(tmp_settings.spend_path)
    assert first["total_usd"] == pytest.approx(sum(c.duration_s for c in clips) * 0.01)
    assert len(first["events"]) == len(clips)
    p2 = PaidFake(cost_per_minute=0.6)
    await run([p2], "verified", clips=clips, settings=tmp_settings, progress=False, stability=False)
    assert p2.calls == 0
    assert load_spend(tmp_settings.spend_path) == first


_WORKER = """
import asyncio, sys
from pathlib import Path
from clipbench.budget import BudgetGuard

async def main(path, n):
    g = BudgetGuard(Path(path), cap=1.0, run_id=sys.argv[3])
    won = 0
    for i in range(n):
        if await g.reserve(0.05):
            await asyncio.sleep(0.001)
            await g.record("p", f"{sys.argv[3]}-{i}", 0.05, 0.05)
            won += 1
    print(won)

asyncio.run(main(sys.argv[1], int(sys.argv[2])))
"""


def test_guard_cap_holds_across_processes(tmp_path):
    """Two clipbench processes sharing one spend.json must not both reserve past the cap or drop each
    other's events (the old guard kept the ledger in memory and the last writer won)."""
    import subprocess
    import sys

    path = tmp_path / "data" / "spend.json"
    script = tmp_path / "worker.py"
    script.write_text(_WORKER)
    import os

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    procs = [
        subprocess.Popen([sys.executable, str(script), str(path), "40", f"r{i}"], stdout=subprocess.PIPE,
                         text=True, env=env)  # fmt: skip
        for i in range(2)
    ]
    won = [int(p.communicate(timeout=120)[0].strip()) for p in procs]
    data = load_spend(path)
    assert sum(won) == 20  # $1.00 cap / $0.05 per request
    assert data["total_usd"] == pytest.approx(1.0)
    assert len(data["events"]) == 20
    assert "reserved" not in data
    assert (tmp_path / "data" / "spend.json.lock").exists()


async def test_guard_sees_other_guards_reservation(tmp_path):
    path = tmp_path / "spend.json"
    a = BudgetGuard(path, cap=1.0, run_id="a")
    b = BudgetGuard(path, cap=1.0, run_id="b")
    assert await a.reserve(0.7)
    assert not await b.reserve(0.5)  # a's open 0.7 counts against the shared cap
    await a.release(0.7)
    assert await b.reserve(0.5)


async def test_guard_ignores_reservation_of_dead_process(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text(json.dumps({"total_usd": 0.0, "by_provider": {}, "events": [],
                                "reserved": {"x": {"usd": 0.9, "pid": 2**22 + 12345}}}))  # fmt: skip
    g = BudgetGuard(path, cap=1.0, run_id="r")
    assert await g.reserve(0.5)


async def test_guard_works_without_fcntl(tmp_path, monkeypatch):
    import clipbench.budget as budget

    monkeypatch.setattr(budget, "fcntl", None)
    g = BudgetGuard(tmp_path / "spend.json", cap=1.0, run_id="r")
    assert await g.reserve(0.4)
    await g.record("p", "v", 0.4, 0.4)
    assert load_spend(tmp_path / "spend.json")["total_usd"] == pytest.approx(0.4)
