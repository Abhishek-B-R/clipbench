"""Speed and compute (A9). No model weights are loaded; the worker subprocess is exercised with a provider
that fails fast."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench import compute
from clipbench.runner import ROW_COLUMNS, _rows_frame


def test_machine_info_and_id():
    info = compute.machine_info()
    for key in ("chip", "cores_total", "cores_performance", "cores_efficiency", "ram_bytes", "os", "python"):
        assert key in info
    assert info["cores_total"] and info["cores_total"] > 0
    assert compute.machine_id(info) == compute.machine_id(dict(info))
    assert compute.machine_id(info) != compute.machine_id({**info, "chip": "other"})


def test_model_size_from_hf_cache(tmp_path):
    blobs = tmp_path / "models--mlx-community--whisper-large-v3-turbo" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a").write_bytes(b"x" * 1000)
    (blobs / "b").write_bytes(b"x" * 500)
    (blobs / "c.incomplete").write_bytes(b"x" * 99)
    assert compute.model_size_bytes("mlx-community/whisper-large-v3-turbo", "mlx", tmp_path) == 1500
    assert compute.model_size_bytes("large-v3", "faster-whisper", tmp_path) is None
    assert compute.hf_repo_for("large-v3", "faster-whisper") == "Systran/faster-whisper-large-v3"


def test_worker_subprocess_reports_error_and_memory(tmp_path):
    # The fake provider needs a clip, so it raises inside the worker: a real subprocess, no model load.
    res = compute.run_worker("fake", [{"path": str(tmp_path / "none.wav"), "seconds": 1.0}], "ja", timeout_s=120)
    assert res["status"] == "error"
    assert res["peak_rss_bytes"] > 1_000_000  # a Python process uses megabytes


def test_bench_rows_rtfx():
    def fake_worker(provider, clips, language):
        return {"status": "ok", "model_id": "mlx-community/whisper-large-v3-mlx", "backend": "mlx", "warmup_s": 9.0,
                "provider_version": "mlx-whisper 0.4", "peak_rss_bytes": 2_000_000_000, "peak_gpu_bytes": 3_000_000_000,
                "clips": [{"audio_s": 30.0, "wall_s": 1.0}, {"audio_s": 60.0, "wall_s": 4.0}]}  # fmt: skip

    info = {"chip": "Test Chip", "cores_total": 8, "ram_bytes": 16 * 2**30, "os": "TestOS", "python": "3.12"}
    df = compute.bench_rows(["whisper-local"], [{"path": "a", "seconds": 1}], "ja", worker=fake_worker, info=info,
                            bench_id="b1")  # fmt: skip
    r = df.iloc[0]
    assert list(df.columns) == compute.BENCH_COLUMNS
    assert r["rtfx"] == pytest.approx(90 / 5)
    assert r["rtfx_median"] == pytest.approx((30 + 15) / 2)
    assert r["chip"] == "Test Chip" and r["n_clips"] == 2 and r["peak_gpu_bytes"] == 3_000_000_000


def _run(tmp_settings, run_id="r1"):
    def row(provider, vid, latency, audio, cost, cached=False, version=None, model="m"):
        return {"run_id": run_id, "provider": provider, "model_id": model, "provider_version": version,
                "video_id": vid, "language": "ja", "tier": "verified", "repeat": 0, "status": "ok", "error": None,
                "latency_ms": latency, "audio_seconds": audio, "cost_usd": cost, "cached": cached,
                "duration_s": audio, "channel": "c", "tags": "[]", "hyp_chars": 10, "retries": 0,
                "timed_out": False, "http_status": None, "detected_language": None, "options": "{}"}  # fmt: skip

    rows = [
        row("deepgram", "a", 2000, 30, 0.002),
        row("deepgram", "b", 1000, 30, 0.002),
        row("deepgram", "c", 50, 30, 0.002, cached=True),  # cached: price counts, speed does not
        row("whisper-local", "a", 1000, 30, 0.0, version="mlx-whisper 0.4", model="mlx-community/whisper-large-v3-mlx"),
        row("whisper-local", "b", 3000, 30, 0.0, version="mlx-whisper 0.4", model="mlx-community/whisper-large-v3-mlx"),
    ]
    d = tmp_settings.runs_dir / run_id
    d.mkdir(parents=True)
    _rows_frame(rows)[ROW_COLUMNS].to_parquet(d / "rows.parquet")
    (d / "run_meta.json").write_text(json.dumps({"language": "ja", "providers": [
        {"id": "deepgram", "paid": True, "uploads_audio": True, "usd_per_minute": 0.0043},
        {"id": "whisper-local", "paid": False, "uploads_audio": False, "usd_per_minute": 0.0}]}))  # fmt: skip


def test_compute_table_and_export(tmp_settings):
    _run(tmp_settings)
    bench = compute.bench_rows(
        ["whisper-local"], [], "ja", bench_id="b1",
        info={"chip": "Test Chip", "cores_total": 8, "ram_bytes": 1, "os": "TestOS", "python": "3.12"},
        worker=lambda p, c, lang: {"status": "ok", "model_id": "mlx-community/whisper-large-v3-mlx", "backend": "mlx",
                                   "peak_rss_bytes": 5, "clips": [{"audio_s": 20.0, "wall_s": 1.0}]},
    )  # fmt: skip
    compute.append_bench(bench, tmp_settings)
    compute.append_bench(bench, tmp_settings)  # same bench id: replaced, not duplicated
    assert len(pd.read_parquet(compute.bench_path(tmp_settings))) == 1

    df = compute.compute_table("r1", tmp_settings).set_index("provider")
    assert list(compute.compute_table("r1", tmp_settings).columns) == compute.COMPUTE_COLUMNS
    dg = df.loc["deepgram"]
    assert dg["kind"] == "api" and dg["n_rows"] == 2
    assert dg["speed_factor"] == pytest.approx(60 / 3)
    assert dg["usd_per_1000_min"] == pytest.approx(4.3)
    assert dg["effective_usd_per_1000_min"] == pytest.approx(0.006 / 1.5 * 1000)
    wl = df.loc["whisper-local"]
    assert wl["kind"] == "local" and pd.isna(wl["usd_per_1000_min"])
    assert wl["speed_factor"] == pytest.approx(60 / 4)
    assert wl["rtfx"] == pytest.approx(20) and wl["chip"] == "Test Chip" and wl["backend"] == "mlx"

    section = compute.export_section("r1", tmp_settings)
    assert {r["provider"] for r in section["compute"]["rows"]} == {"deepgram", "whisper-local"}
    assert section["compute"]["machines"][0]["chip"] == "Test Chip"
    json.dumps(section)
    path = compute.write_compute("r1", tmp_settings)
    assert path == tmp_settings.results_dir / "r1" / "compute.parquet" and path.exists()
    assert compute.export_section("missing", tmp_settings) is None
