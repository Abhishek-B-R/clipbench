from __future__ import annotations

import csv
import json
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from clipbench import submit
from clipbench.canary import Candidate, write_canary
from clipbench.models import Clip, Result
from tests.fixtures.make_clips import make_clips

FIXTURE = Path(__file__).parent / "fixtures" / "submission_adapter.py"


def _report(class_name: str) -> dict[str, Any]:
    rep = submit.validate_file(FIXTURE, class_name)
    return {c.name: c for c in rep.checks} | {"_ok": rep.ok}


def test_good_api_adapter_passes(monkeypatch):
    monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)
    r = _report("ExampleApi")
    assert r["_ok"], {k: v for k, v in r.items() if k != "_ok" and v.status == "fail"}
    assert r["result_shape"].status == "pass"
    assert r["word_timings"].status == "pass"
    assert r["error_handling_network"].status == "pass"
    assert "blocked attempts" in r["error_handling_network"].detail
    assert r["no_network"].status == "pass"


def test_local_model_is_checked_through_transcribe():
    r = _report("LocalModel")
    assert r["_ok"]
    assert r["error_handling_network"].detail == "transcribe() works offline (local model)"


@pytest.mark.parametrize(
    ("class_name", "check"),
    [
        ("SaysAvailableWithoutKeys", "availability_without_keys"),
        ("BadTimings", "result_shape"),
        ("SwallowsErrors", "error_handling_malformed"),
        ("SwallowsErrors", "error_handling_network"),
        ("NoFixture", "result_shape"),
        ("FreeButPaid", "pricing"),
        ("ClashesWithRegistry", "id"),
        ("NotAProvider", "class"),
        ("Missing", "import"),
    ],
)
def test_contract_failures(class_name, check):
    r = _report(class_name)
    assert not r["_ok"]
    assert r[check].status == "fail"


def test_network_guard_blocks_and_restores():
    original = socket.getaddrinfo
    with submit.no_network() as attempts, pytest.raises(submit.NetworkBlocked):
        socket.create_connection(("example.invalid", 80))
    assert attempts == ["create_connection"]
    assert socket.getaddrinfo is original


def test_import_time_network_is_refused(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import socket\nsocket.getaddrinfo('example.invalid', 80)\n")
    rep = submit.validate_file(bad, "X")
    assert not rep.ok and rep.checks[0].name == "import"


def test_result_problems_catch_bad_values():
    res = Result(text="x", latency_ms=-1, audio_seconds=9.0, cost_usd=0.0, model_id="", http_status=42,
                 words=None, speakers=["A"])  # fmt: skip
    bad, warn = submit.result_problems(res, 2.0)
    assert any("model_id" in b for b in bad) and any("latency" in b for b in bad)
    assert any("audio_seconds" in b for b in bad) and any("http_status" in b for b in bad)
    assert any("speakers" in b for b in bad)
    assert warn


# ---------------------------------------------------------------- run and bundle


@pytest.fixture
def harness(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_CANARY_DIR", str(tmp_path / "canary"))
    monkeypatch.setenv("CLIPBENCH_SUBMISSIONS_DIR", str(tmp_path / "submissions"))
    clips = make_clips(tmp_settings.audio_dir)
    picked = [Candidate(clip_id=c.video_id, language="ja", channel=c.channel) for c in clips]
    picked.append(Candidate(clip_id="nofile00001", language="ja"))
    write_canary("ja", picked, 7, tmp_settings)
    return tmp_settings


def _example(monkeypatch):
    cls = submit.load_adapter(FIXTURE, "ExampleApi")

    class Offline(cls):  # type: ignore[misc, valid-type]
        id: ClassVar[str] = "example-api"

        async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
            if clip is not None and clip.video_id == "fixture0003":
                raise RuntimeError("server said no")
            res = self.parse_response(self.fixture_response, 2.0)
            res.latency_ms = 12.0
            return res

    return Offline()


def test_plan_and_bundle(harness, monkeypatch):
    provider = _example(monkeypatch)
    p = submit.plan(provider, "ja", harness)
    assert len(p.clips) == 5 and p.missing == ["nofile00001"]
    assert p.estimate_usd == pytest.approx(0.004 * 20.5 / 60)
    import asyncio

    rows = asyncio.run(submit.run_submission(provider, p.clips, "ja", max_usd=1.0))
    assert [r["status"] for r in rows].count("error") == 1
    assert next(r for r in rows if r["status"] == "error")["error_type"] == "RuntimeError"
    report = submit.validate_file(FIXTURE, "ExampleApi", "ja")
    out = submit.write_bundle("example-api", FIXTURE, "ExampleApi", provider, "ja", rows, report, 1, harness)
    assert {p.name for p in out.iterdir()} == submit.BUNDLE_FILES
    assert submit.check_bundle(out, harness) == []
    text = (out / "rows.csv").read_text() + (out / "submission.json").read_text()
    assert "tone test" not in text  # hypothesis text never lands in the bundle, only its hash
    meta = json.loads((out / "submission.json").read_text())
    assert meta["summary"]["n_ok"] == 4 and meta["scored"] is False

    # Tampering is caught: a changed adapter, an edited number, a text column.
    (out / "adapter.py").write_text((out / "adapter.py").read_text() + "\n# edited\n")
    assert any("adapter_sha256" in p for p in submit.check_bundle(out, harness))
    submit.write_bundle("example-api", FIXTURE, "ExampleApi", provider, "ja", rows, report, 1, harness)
    lines = list(csv.DictReader((out / "rows.csv").open()))
    lines[0]["cost_usd"] = "0.5"
    with (out / "rows.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=submit.ROW_FIELDS)
        w.writeheader()
        w.writerows(lines)
    assert any("summary.cost_usd_total" in p for p in submit.check_bundle(out, harness))
    (out / "texts.csv").write_text("hyp\n")
    assert any("unexpected files" in p for p in submit.check_bundle(out, harness))


def test_budget_cap_skips_instead_of_paying(harness, monkeypatch):
    import asyncio

    provider = _example(monkeypatch)
    p = submit.plan(provider, "ja", harness)
    rows = asyncio.run(submit.run_submission(provider, p.clips, "ja", max_usd=0.0005))
    assert any(r["error_type"] == "budget" for r in rows)
    assert sum(float(r["cost_usd"] or 0) for r in rows) <= 0.0005 + 1e-9


def _app():
    import typer

    app = typer.Typer()

    @app.callback()
    def _root() -> None: ...

    submit.register(app)
    return app


def test_cli_validate_and_dry_run(harness, monkeypatch):
    monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)
    runner = CliRunner()
    res = runner.invoke(_app(), ["submit", "--adapter", f"{FIXTURE}:ExampleApi", "--validate-only"])
    assert res.exit_code == 0, res.output
    assert "contract: passed" in res.output
    res = runner.invoke(_app(), ["submit", "--adapter", f"{FIXTURE}:ExampleApi", "--lang", "ja"])
    assert res.exit_code == 0, res.output
    assert "dry run: no request was made" in res.output and "1 missing" in res.output
    res = runner.invoke(_app(), ["submit", "--adapter", f"{FIXTURE}:ExampleApi", "--lang", "ja", "--run"])
    assert res.exit_code == 1  # no EXAMPLE_API_KEY: refuses before any request
    res = runner.invoke(_app(), ["submit", "--adapter", f"{FIXTURE}:BadTimings", "--validate-only"])
    assert res.exit_code == 1 and "contract: FAILED" in res.output
    res = runner.invoke(_app(), ["submit", "--adapter", f"{FIXTURE}:ExampleApi", "--lang", "ko"])
    assert res.exit_code == 1 and "no canary set for ko" in res.output


def test_cli_check_all_without_bundles(harness):
    res = CliRunner().invoke(_app(), ["submission-check", "--all"])
    assert res.exit_code == 0 and "no submission bundles" in res.output


def test_loading_an_adapter_never_writes_bytecode_into_its_folder(tmp_path, monkeypatch):
    # CI runs without PYTHONDONTWRITEBYTECODE; a __pycache__ in a bundle used to hide the hash check.
    import shutil
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    adapter = tmp_path / "adapter.py"
    shutil.copy(FIXTURE, adapter)
    submit.load_adapter(adapter, "ExampleApi")
    assert not (tmp_path / "__pycache__").exists()
    assert sys.dont_write_bytecode is False  # restored afterwards
