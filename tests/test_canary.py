from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar

import pandas as pd
import pytest
from rich.console import Console
from typer.testing import CliRunner

from clipbench import canary
from clipbench.canary import (
    CanaryBudgetExceeded,
    CanaryError,
    Candidate,
    NotEnoughVerified,
    append_history,
    drift_events,
    load_canary,
    load_history,
    run_canary,
    select_and_freeze,
    select_canary,
)
from clipbench.providers.fake import FakeProvider
from tests.fixtures.make_clips import make_clips


@pytest.fixture
def canary_env(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_CANARY_DIR", str(tmp_path / "canary"))
    monkeypatch.delenv("CLIPBENCH_CANARY_BUDGET_USD", raising=False)
    monkeypatch.delenv("CLIPBENCH_CANARY_CADENCE_DAYS", raising=False)
    return tmp_settings


def _cands(n: int, channels: int = 40) -> list[Candidate]:
    return [Candidate(clip_id=f"vid{i:04d}", language="ja", channel=f"ch{i % channels}") for i in range(n)]


def test_selection_is_deterministic_and_order_free():
    cands = _cands(80)
    a = select_canary(cands, n=30, seed=7)
    b = select_canary(list(reversed(cands)), n=30, seed=7)
    assert [c.clip_id for c in a] == [c.clip_id for c in b]
    assert len(a) == 30
    assert [c.clip_id for c in select_canary(cands, n=30, seed=8)] != [c.clip_id for c in a]


def test_selection_caps_channels_then_fills():
    spread = select_canary(_cands(200, channels=50), n=30, seed=7)
    counts = pd.Series([c.channel for c in spread]).value_counts()
    assert counts.max() <= canary.MAX_PER_CHANNEL
    # Two channels only: the cap cannot be met, so the set is filled from the rest.
    few = select_canary(_cands(40, channels=2), n=30, seed=7)
    assert len(few) == 30


def test_selection_needs_enough_verified():
    with pytest.raises(NotEnoughVerified):
        select_canary(_cands(10), n=30)


def test_select_without_verified_clips_says_so(canary_env):
    with pytest.raises(NotEnoughVerified, match="no verified ja clips"):
        select_and_freeze("ja", canary_env)
    res = CliRunner().invoke(_app(), ["canary", "select", "--lang", "ja"])
    assert res.exit_code == 1
    assert "no verified ja clips" in res.output


def test_freeze_and_reselect(canary_env):
    path, picked = select_and_freeze("ja", canary_env, candidates=_cands(60))
    assert len(load_canary("ja", canary_env)) == 30
    assert path.read_text().splitlines()[0].startswith("clip_id,language,platform")
    with pytest.raises(CanaryError, match="frozen"):
        select_and_freeze("ja", canary_env, candidates=_cands(60))
    select_and_freeze("ja", canary_env, candidates=_cands(60), reselect=True)
    assert load_canary("ja", canary_env) == [c.clip_id for c in picked]


def _hist(points):
    return pd.DataFrame(
        [
            {"date": d, "provider": p, "model_id": m, "provider_version": v, "metric": "cer", "error_rate": e,
             "ci_low": lo, "ci_high": hi, "n": 30, "run_id": f"canary-ja-{d}"}
            for d, p, m, v, e, lo, hi in points
        ]
    )  # fmt: skip


def test_drift_rules():
    h = _hist(
        [
            ("2026-10-01", "a", "m1", "v1", 0.10, 0.08, 0.12),
            ("2026-10-08", "a", "m1", "v1", 0.11, 0.09, 0.13),  # inside previous CI: no event
            ("2026-10-15", "a", "m1", "v2", 0.20, 0.17, 0.23),  # outside + version change
            ("2026-10-22", "a", "m2", "v2", 0.19, 0.16, 0.22),  # model change only
            ("2026-10-01", "b", "x", None, 0.30, 0.25, 0.35),
        ]
    )
    ev = drift_events("ja", h)
    kinds = sorted(zip(ev["date"], ev["kind"], strict=True))
    assert kinds == [("2026-10-15", "provider_version"), ("2026-10-15", "score"), ("2026-10-22", "model_id")]
    score = ev[ev["kind"] == "score"].iloc[0]
    assert (score["before"], score["after"], score["ci_low"], score["ci_high"]) == (0.11, 0.20, 0.09, 0.13)
    assert drift_events("ja", h.iloc[:0]).empty


def test_history_is_append_only(canary_env):
    first = _hist([("2026-10-01", "a", "m1", "v1", 0.1, 0.08, 0.12)])
    append_history("ja", first, canary_env)
    again = first.assign(error_rate=0.9)  # same run id: ignored, the old row is untouched
    append_history("ja", again, canary_env)
    append_history("ja", _hist([("2026-10-08", "a", "m1", "v1", 0.2, 0.18, 0.22)]), canary_env)
    h = load_history("ja", canary_env)
    assert h["error_rate"].tolist() == [0.1, 0.2]
    assert list(h.columns) == canary.HISTORY_COLUMNS


def test_monthly_budget_counts_canary_events_only():
    spend = {
        "events": [
            {"run_id": "canary-ja-20261001-0300", "usd": 0.5, "at": "2026-10-01T03:00:00Z"},
            {"run_id": "canary-ja-20260930-0300", "usd": 9.0, "at": "2026-09-30T03:00:00Z"},
            {"run_id": "20261001-0300-abcd", "usd": 7.0, "at": "2026-10-01T03:00:00Z"},
        ]
    }
    assert canary.canary_spent(spend, "2026-10") == 0.5
    canary.check_month(1.4, 0.5, 2.0, "2026-10")
    with pytest.raises(CanaryBudgetExceeded, match="monthly canary cap"):
        canary.check_month(1.6, 0.5, 2.0, "2026-10")


def test_settings_from_toml_and_env(canary_env, monkeypatch):
    assert canary.monthly_cap() == 2.0
    assert canary.cadence_days() == 7
    monkeypatch.setenv("CLIPBENCH_CANARY_BUDGET_USD", "0.5")
    monkeypatch.setenv("CLIPBENCH_CANARY_CADENCE_DAYS", "3")
    assert canary.monthly_cap() == 0.5
    assert canary.cadence_days() == 3


def test_due(canary_env, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_CANARY_CADENCE_DAYS", "7")
    assert canary.is_due("ja", canary_env)
    append_history("ja", _hist([("2026-10-01", "a", "m1", "v1", 0.1, 0.08, 0.12)]), canary_env)
    assert not canary.is_due("ja", canary_env, today=datetime(2026, 10, 7, tzinfo=UTC))
    assert canary.is_due("ja", canary_env, today=datetime(2026, 10, 8, tzinfo=UTC))


class PaidFake(FakeProvider):
    id: ClassVar[str] = "fake-paid"
    paid: ClassVar[bool] = True


def _freeze_fixture_set(settings, clips):
    picked = [Candidate(clip_id=c.video_id, language="ja", channel=c.channel) for c in clips]
    canary.write_canary("ja", picked, 7, settings)


async def test_run_writes_history_and_drift(canary_env, tmp_path):
    clips = make_clips(tmp_path / "audio")
    _freeze_fixture_set(canary_env, clips)
    console = Console(stderr=True, quiet=True)
    t1 = datetime(2026, 10, 1, 3, 17, tzinfo=UTC)
    res = await run_canary("ja", [FakeProvider(sub_rate=0.0, del_rate=0.0, ins_rate=0.0)], settings=canary_env,
                           clips=clips, console=console, now=t1)  # fmt: skip
    assert res.run_id == "canary-ja-20261001-0317"
    # Kept apart from the benchmark runs, so `latest` never picks a canary.
    assert not (canary_env.runs_dir / res.run_id).exists()
    assert (canary_env.results_dir / "canary" / "runs" / res.run_id / "rows.parquet").exists()
    h = load_history("ja", canary_env)
    assert set(h["provider"]) == {"fake"} and (h["n"] == 5).all()
    assert (h["error_rate"] == 0.0).all()

    t2 = datetime(2026, 10, 8, 3, 17, tzinfo=UTC)
    res2 = await run_canary("ja", [FakeProvider(sub_rate=0.5, del_rate=0.0, ins_rate=0.0)], settings=canary_env,
                            clips=clips, console=console, now=t2)  # fmt: skip
    assert "score" in set(res2.events["kind"])
    assert len(load_history("ja", canary_env)) == 2 * len(h)
    drift = pd.read_parquet(canary_env.results_dir / "history" / "drift.parquet")
    assert (drift["date"] == "2026-10-08").all()


async def test_run_refuses_over_monthly_cap(canary_env, tmp_path, monkeypatch):
    clips = make_clips(tmp_path / "audio")
    _freeze_fixture_set(canary_env, clips)
    monkeypatch.setenv("CLIPBENCH_CANARY_BUDGET_USD", "0.01")
    canary_env.spend_path.parent.mkdir(parents=True, exist_ok=True)
    canary_env.spend_path.write_text(json.dumps({"total_usd": 0.0, "by_provider": {}, "events": []}))
    paid = PaidFake(cost_per_minute=1.0)  # 20.5 s of audio = $0.34
    console = Console(stderr=True, quiet=True)
    dry = await run_canary("ja", [paid], settings=canary_env, clips=clips, dry_run=True, console=console)
    assert dry.estimate_usd > 0.01 and dry.dry_run
    with pytest.raises(CanaryBudgetExceeded):
        await run_canary("ja", [paid], settings=canary_env, clips=clips, console=console)
    assert paid.calls == 0


async def test_run_spend_is_recorded_by_month(canary_env, tmp_path, monkeypatch):
    clips = make_clips(tmp_path / "audio")
    _freeze_fixture_set(canary_env, clips)
    monkeypatch.setenv("CLIPBENCH_CANARY_BUDGET_USD", "5")
    paid = PaidFake(cost_per_minute=0.06)
    now = datetime.now(UTC)
    await run_canary("ja", [paid], settings=canary_env, clips=clips, console=Console(quiet=True), now=now)
    spend = json.loads(canary_env.spend_path.read_text())
    month = now.strftime("%Y-%m")
    assert spend["canary_by_month"][month] == pytest.approx(0.0205, abs=1e-6)
    assert all(e["run_id"].startswith("canary-ja-") for e in spend["events"])


async def test_run_without_a_set_says_so(canary_env):
    with pytest.raises(CanaryError, match="no canary set for ja"):
        await run_canary("ja", [FakeProvider()], settings=canary_env, console=Console(quiet=True))


def test_export_section(canary_env):
    run_id = "20261019-1000-abcd"
    meta = canary_env.runs_dir / run_id / "run_meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"language": "ja"}))
    assert canary.export_section(run_id, canary_env) is None
    append_history("ja", _hist([("2026-10-01", "a", "m1", "v1", 0.1, 0.08, 0.12),
                                ("2026-10-08", "a", "m1", "v1", 0.3, 0.28, 0.32)]), canary_env)  # fmt: skip
    canary.rebuild_drift(canary_env)
    sec = canary.export_section(run_id, canary_env)
    assert sec["history"]["language"] == "ja"
    assert [p["date"] for p in sec["history"]["series"]["a"]] == ["2026-10-01", "2026-10-08"]
    assert sec["drift"][0]["kind"] == "score" and sec["drift"][0]["ci"] == [0.08, 0.12]
    json.dumps(sec, allow_nan=False)


def test_due_cli(canary_env):
    res = CliRunner().invoke(_app(), ["canary", "due", "--lang", "all"])
    assert res.exit_code == 0
    assert res.stdout.strip() == ""


def _app():
    import typer

    app = typer.Typer()

    @app.callback()
    def _root() -> None: ...

    canary.register(app)
    return app
