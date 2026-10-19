from __future__ import annotations

import json
import re

import pandas as pd
import pytest
from typer.testing import CliRunner

from clipbench.report import export, publish
from tests.test_report import PROVIDERS, _write_run

RUN_KEYS = {
    "run_id", "created_at", "language", "git_commit", "sample_seed", "metrics", "providers", "aggregates",
    "paired", "stability", "findings", "errors", "clips", "per_clip", "limitations",
}  # fmt: skip


@pytest.fixture
def web_settings(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPBENCH_WEB_DIR", str(tmp_path / "web"))
    return tmp_settings


def _write_scores(settings, run_id="r1"):
    rows, run_rows = [], []
    for pid, (model, er, _cost, lat) in PROVIDERS.items():
        for i in range(3):
            vid = f"v{i}"
            for metric, primary in (("cer", True), ("cer_reading", False)):
                rows.append(
                    {
                        "provider": pid, "model_id": model, "video_id": vid, "tier": "verified",
                        "language": "ja", "metric": metric, "is_primary": primary, "error_rate": er,
                        "hallucination_rate": 0.0, "latency_ms": lat, "duration_s": 20.0 + i,
                        "tags": json.dumps(["background_music"] if i == 0 else []),
                    }
                )  # fmt: skip
            run_rows.append(
                {"run_id": run_id, "provider": pid, "video_id": vid, "channel": f"ch{i}", "repeat": 0}
            )
    pd.DataFrame(rows).to_parquet(settings.results_dir / run_id / "scores.parquet")
    pd.DataFrame(run_rows).to_parquet(settings.runs_dir / run_id / "rows.parquet")


def test_export_writes_contract(web_settings):
    _write_run(web_settings)
    _write_scores(web_settings)
    run_path, index_path = export.export_run("latest", web_settings)
    assert run_path.name == "r1.json" and run_path.parent.name == "runs"

    run = json.loads(run_path.read_text())
    assert set(run) == RUN_KEYS
    assert run["run_id"] == "r1" and run["language"] == "ja" and run["git_commit"] == "abc1234"
    assert run["sample_seed"] == 7 and run["created_at"] == "2026-10-19T10:00:00Z"
    assert run["metrics"][0] == {"name": "cer", "primary": True, "label": "Character error rate"}
    assert [m["name"] for m in run["metrics"]] == ["cer", "cer_reading"]

    provs = {p["id"]: p for p in run["providers"]}
    assert set(provs) == set(PROVIDERS)
    assert provs["alpha"]["model_id"] == "alpha-1" and provs["alpha"]["pricing_checked_on"] == "2026-10-19"
    assert set(provs["alpha"]) == {
        "id",
        "model_id",
        "label",
        "pricing_checked_on",
        "usd_per_hour",
        "paid",
        "uploads_audio",
    }

    assert run["aggregates"] and {"provider", "error_rate", "ci_low", "too_small"} <= set(
        run["aggregates"][0]
    )
    assert run["paired"] and run["paired"][0]["verdict"] in {"a_better", "b_better", "no_difference"}
    assert len(run["stability"]) == 6

    assert run["findings"]
    for f in run["findings"]:
        assert set(f) == {"tier", "text", "source"}
        assert f["text"].startswith("DRAFT: verify.") and "(source:" not in f["text"]
        assert f["source"].split(",")[0].endswith(".parquet")
    assert {f["tier"] for f in run["findings"]} == {"verified", "creator"}
    assert any("alpha beats gamma" in f["text"] for f in run["findings"])

    assert run["errors"]["providers"]["alpha"]["top_substitutions"] == [["きょう", "今日", 3]]
    assert run["errors"]["examples"][0]["video_id"] == "v1"
    assert set(run["errors"]) == {"providers", "examples"}

    clips = {c["video_id"]: c for c in run["clips"]}
    assert clips["v0"] == {"video_id": "v0", "tier": "verified", "duration_s": 20.0, "channel": "ch0",
                           "tags": ["background_music"]}  # fmt: skip
    assert len(run["per_clip"]) == 3 * 3 * 2
    assert set(run["per_clip"][0]) == {
        "provider", "video_id", "tier", "metric", "error_rate", "hallucination_rate", "latency_ms",
    }  # fmt: skip
    assert any("YouTube auto captions and Wayin" in line for line in run["limitations"])

    index = json.loads(index_path.read_text())
    assert index["latest"] == "r1" and "demo" not in index
    entry = index["runs"][0]
    assert entry == {
        "run_id": "r1", "created_at": "2026-10-19T10:00:00Z", "language": "ja", "tiers": ["verified", "creator"],
        "n_clips": {"verified": 60, "creator": 60}, "providers": entry["providers"],
    }  # fmt: skip
    assert set(entry["providers"]) == set(PROVIDERS)

    raw = run_path.read_text() + index_path.read_text()
    assert "NaN" not in raw
    assert not re.search("[" + chr(0x2013) + chr(0x2014) + "]", raw)


def test_export_merges_index_and_drops_demo(web_settings):
    _write_run(web_settings)
    out = export.data_dir(web_settings)
    out.mkdir(parents=True)
    (out / "index.json").write_text(
        json.dumps({"demo": True, "latest": "demo", "runs": [{"run_id": "demo"}]})
    )
    export.export_run("r1", web_settings)
    index = json.loads((out / "index.json").read_text())
    assert [r["run_id"] for r in index["runs"]] == ["r1"]

    # A second, older run is merged in; latest stays the newest by created_at.
    old = dict(index["runs"][0], run_id="r0", created_at="2026-01-01T00:00:00Z")
    index["runs"].append(old)
    (out / "index.json").write_text(json.dumps(index))
    export.export_run("r1", web_settings)
    index = json.loads((out / "index.json").read_text())
    assert [r["run_id"] for r in index["runs"]] == ["r1", "r0"] and index["latest"] == "r1"
    # Without scores.parquet, clips and per_clip are empty lists, not missing.
    run = json.loads((out / "runs" / "r1.json").read_text())
    assert run["clips"] == [] and run["per_clip"] == []


def test_split_source():
    text, src = export.split_source(
        "DRAFT: verify. a has the lowest cer (source: aggregates.parquet, tier=verified)."
    )
    assert text == "DRAFT: verify. a has the lowest cer." and src == "aggregates.parquet, tier=verified"


def test_cli_export(web_settings):
    from clipbench.cli import app

    _write_run(web_settings)
    res = CliRunner().invoke(app, ["export", "r1"])
    assert res.exit_code == 0, res.output
    assert (export.data_dir(web_settings) / "runs" / "r1.json").exists()
    missing = CliRunner().invoke(app, ["export", "nope"])
    assert missing.exit_code == 1


def test_publish_check_scans_web_data(web_settings):
    ref = "hoy vamos a aprender diez palabras nuevas para hablar con tus amigos en la calle"
    runs = web_settings.runs_text_dir / "r1"
    runs.mkdir(parents=True)
    pd.DataFrame(
        [{"video_id": "es1", "tier": "verified", "ref_text": ref, "words_json": None, "tags": "[]"}]
    ).to_parquet(runs / "refs.parquet")
    data = export.data_dir(web_settings)
    (data / "runs").mkdir(parents=True)
    (data / "runs" / "ok.json").write_text(json.dumps({"snippet": "hoy vamos a"}))
    assert [o for o in publish.check(web_settings) if o.path.name != ".gitignore"] == []

    (data / "runs" / "leak.json").write_text(json.dumps({"x": ref}))
    dist = export.web_dir(web_settings) / "dist" / "assets"
    dist.mkdir(parents=True)
    (dist / "clip.mp3").write_bytes(b"ID3\x00")
    names = {o.path.name for o in publish.check(web_settings) if o.path.name != ".gitignore"}
    assert names == {"leak.json", "clip.mp3"}
