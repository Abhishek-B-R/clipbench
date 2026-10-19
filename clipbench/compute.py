"""Speed and compute (A9): RTFx, memory and model size for local open models; price and speed for APIs.

Two parts:

1. `clipbench compute bench --providers whisper-local,whisper-local-turbo --lang L --limit N` measures the
   local models on this machine. Each model runs in its own subprocess (so its memory is its own), loads,
   transcribes one warm up clip (timed separately as `warmup_s`, it includes loading the weights), then
   transcribes the N clips one after another. Per model it records:
   - `rtfx` = total audio seconds / total wall seconds over the N clips (after warm up). Higher is faster;
     100 means 100 s of audio per second.
   - `peak_rss_bytes`: the subprocess's peak resident memory (`ru_maxrss` from `os.wait4`, so it is that
     process only; bytes on macOS, KiB converted on Linux). On Apple silicon the GPU shares this memory but
     Metal buffers are not always counted in RSS, so `peak_gpu_bytes` adds MLX's own peak allocation
     (`mlx.core.get_peak_memory`) when the backend is mlx. psutil is not a dependency, so nothing else.
   - `model_size_bytes`: size on disk of the weights in the Hugging Face cache (the repo's blobs).
   - the declared machine: chip, total, performance and efficiency cores, RAM, OS, Python, backend
     (mlx or faster-whisper on CPU), and `machine_id` (a hash of those, so runs on one machine group).
   Rows are appended to results/compute/bench.parquet. No text is kept.
   Metal is not available inside a sandboxed shell, so on a Mac run the bench from a normal terminal or
   the mlx backend falls back to faster-whisper on CPU (the backend column says which one ran).

2. `clipbench compute table <run_id>` writes results/<run_id>/compute.parquet from the run's rows:
   - every provider: `speed_factor_median`, the median over non cached ok rows of audio_seconds / latency
     (the headline: robust to a cold start such as the first call downloading weights), and
     `speed_factor` = total audio seconds / total latency seconds over the same rows;
   - APIs: `usd_per_1000_min` (list price from pricing.toml x 1000) and `effective_usd_per_1000_min`
     (sum of cost_usd over audio minutes x 1000, which includes minimum billed lengths);
   - local models: the newest bench row for the same provider, model and backend (rtfx, memory, size,
     machine).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings

LOCAL_PROVIDERS = ("whisper-local", "whisper-local-turbo")

BENCH_COLUMNS = [
    "bench_id", "measured_at", "provider", "model_id", "backend", "machine_id", "chip", "cores_total",
    "cores_performance", "cores_efficiency", "ram_bytes", "os", "python", "n_clips", "audio_seconds",
    "wall_seconds", "rtfx", "rtfx_median", "warmup_s", "peak_rss_bytes", "peak_gpu_bytes", "model_size_bytes",
    "provider_version", "status", "error",
]  # fmt: skip
COMPUTE_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "kind", "n_rows", "audio_seconds", "wall_seconds",
    "speed_factor", "speed_factor_median", "latency_p50_ms", "usd_per_1000_min", "effective_usd_per_1000_min",
    "bench_id", "rtfx", "peak_rss_bytes", "peak_gpu_bytes", "model_size_bytes", "backend", "machine_id",
    "chip", "cores_total", "ram_bytes", "os",
]  # fmt: skip


# ---------------------------------------------------------------- machine


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def machine_info() -> dict[str, Any]:
    """chip, cores, RAM and OS of this machine. Fields it cannot read are None."""
    info: dict[str, Any] = {
        "chip": None,
        "cores_total": os.cpu_count(),
        "cores_performance": None,
        "cores_efficiency": None,
        "ram_bytes": None,
        "os": platform.platform(),
        "python": platform.python_version(),
        "arch": platform.machine(),
    }
    if sys.platform == "darwin":
        info["chip"] = _sysctl("machdep.cpu.brand_string")
        info["ram_bytes"] = _int(_sysctl("hw.memsize"))
        info["cores_performance"] = _int(_sysctl("hw.perflevel0.physicalcpu"))
        info["cores_efficiency"] = _int(_sysctl("hw.perflevel1.physicalcpu"))
        ver = platform.mac_ver()[0]
        if ver:
            info["os"] = f"macOS {ver} ({platform.machine()})"
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    info["chip"] = line.split(":", 1)[1].strip()
                    break
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    info["ram_bytes"] = int(line.split()[1]) * 1024
                    break
        except OSError:
            pass
    if info["ram_bytes"] is None:
        try:
            info["ram_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            pass
    return info


def machine_id(info: dict[str, Any]) -> str:
    blob = json.dumps({k: info.get(k) for k in ("chip", "cores_total", "ram_bytes", "arch")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- model size


def hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def hf_repo_for(model_id: str, backend: str | None) -> str:
    """The Hugging Face repo that holds `model_id`'s weights for this backend."""
    if "/" in model_id:
        return model_id
    try:
        from faster_whisper.utils import _MODELS  # size name -> repo

        return _MODELS.get(model_id, model_id)
    except Exception:
        return {"large-v3": "Systran/faster-whisper-large-v3",
                "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}.get(model_id, model_id)  # fmt: skip


def model_size_bytes(model_id: str, backend: str | None = None, cache: Path | None = None) -> int | None:
    """Bytes of the repo's blobs in the HF cache (each file once), or None when it is not downloaded."""
    repo = hf_repo_for(model_id, backend)
    folder = (cache or hf_cache_dir()) / ("models--" + repo.replace("/", "--"))
    blobs = folder / "blobs"
    if blobs.is_dir():
        files = [p for p in blobs.iterdir() if p.is_file() and not p.name.endswith(".incomplete")]
        return sum(p.stat().st_size for p in files) or None
    if folder.is_dir():
        files = [p for p in folder.rglob("*") if p.is_file()]
        return sum(p.resolve().stat().st_size for p in files) or None
    return None


# ---------------------------------------------------------------- bench


def _peak_gpu_bytes() -> int | None:
    try:
        import mlx.core as mx

        fn = getattr(mx, "get_peak_memory", None) or getattr(getattr(mx, "metal", None), "get_peak_memory", None)
        return int(fn()) if fn else None
    except Exception:
        return None


def _worker(spec_path: str) -> None:
    """Subprocess body: load one provider, warm up, transcribe the clips, print one JSON result."""
    spec = json.loads(Path(spec_path).read_text())
    from clipbench.providers import registry

    out: dict[str, Any] = {"status": "ok", "error": None, "clips": []}
    try:
        p = registry.make(spec["provider"])
        out["model_id"] = p.model_id
        out["backend"] = getattr(p, "backend", None)
        items = spec["clips"]
        if not items:
            raise ValueError("no clips")
        t0 = time.perf_counter()
        first = asyncio.run(p.transcribe(Path(items[0]["path"]), spec["language"]))
        out["warmup_s"] = time.perf_counter() - t0
        out["provider_version"] = first.provider_version
        for item in items[1:] if len(items) > 1 else items:
            t0 = time.perf_counter()
            asyncio.run(p.transcribe(Path(item["path"]), spec["language"]))
            out["clips"].append({"audio_s": float(item["seconds"]), "wall_s": time.perf_counter() - t0})
        out["backend"] = getattr(p, "backend", None)
        out["peak_gpu_bytes"] = _peak_gpu_bytes() if out["backend"] == "mlx" else None
    except Exception as exc:
        out.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    print(json.dumps(out))


def _maxrss_bytes(ru_maxrss: int) -> int:
    return int(ru_maxrss) if sys.platform == "darwin" else int(ru_maxrss) * 1024


def run_worker(provider: str, clips: list[dict[str, Any]], language: str, timeout_s: float = 7200) -> dict[str, Any]:
    """Run _worker in a fresh Python and return its result plus the child's peak RSS."""
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / "spec.json"
        spec.write_text(json.dumps({"provider": provider, "clips": clips, "language": language}))
        out_path = Path(tmp) / "out.json"
        with out_path.open("w") as fh:
            proc = subprocess.Popen([sys.executable, "-m", "clipbench.compute", spec.as_posix()], stdout=fh,
                                    stderr=subprocess.DEVNULL)  # fmt: skip
            deadline = time.monotonic() + timeout_s
            while True:
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    break
                if time.monotonic() > deadline:
                    proc.kill()
                    pid, status, usage = os.wait4(proc.pid, 0)
                    proc.returncode = -9
                    return {"status": "error", "error": f"timeout after {timeout_s:.0f}s", "clips": []}
                time.sleep(0.2)
            proc.returncode = os.waitstatus_to_exitcode(status)
        lines = [ln for ln in out_path.read_text().splitlines() if ln.strip().startswith("{")]
        result = json.loads(lines[-1]) if lines else {"status": "error", "error": "worker printed nothing", "clips": []}
        result["peak_rss_bytes"] = _maxrss_bytes(usage.ru_maxrss)
        return result


def bench_rows(
    providers: list[str],
    clips: list[dict[str, Any]],
    language: str,
    *,
    worker=run_worker,
    info: dict[str, Any] | None = None,
    bench_id: str | None = None,
) -> pd.DataFrame:
    """One row per provider. `clips` are {"path", "seconds"}; the first one is the warm up."""
    info = info or machine_info()
    mid = machine_id(info)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bench_id = bench_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    rows = []
    for pid in providers:
        res = worker(pid, clips, language)
        per = res.get("clips") or []
        audio = float(sum(c["audio_s"] for c in per))
        wall = float(sum(c["wall_s"] for c in per))
        ratios = [c["audio_s"] / c["wall_s"] for c in per if c["wall_s"] > 0]
        model_id = res.get("model_id")
        rows.append({
            "bench_id": bench_id, "measured_at": now, "provider": pid, "model_id": model_id,
            "backend": res.get("backend"), "machine_id": mid, "chip": info.get("chip"),
            "cores_total": info.get("cores_total"), "cores_performance": info.get("cores_performance"),
            "cores_efficiency": info.get("cores_efficiency"), "ram_bytes": info.get("ram_bytes"),
            "os": info.get("os"), "python": info.get("python"), "n_clips": len(per), "audio_seconds": audio,
            "wall_seconds": wall, "rtfx": audio / wall if wall > 0 else None,
            "rtfx_median": float(np.median(ratios)) if ratios else None, "warmup_s": res.get("warmup_s"),
            "peak_rss_bytes": res.get("peak_rss_bytes"), "peak_gpu_bytes": res.get("peak_gpu_bytes"),
            "model_size_bytes": model_size_bytes(model_id, res.get("backend")) if model_id else None,
            "provider_version": res.get("provider_version"), "status": res.get("status"), "error": res.get("error"),
        })  # fmt: skip
    df = pd.DataFrame(rows, columns=BENCH_COLUMNS)
    for col in ("audio_seconds", "wall_seconds", "rtfx", "rtfx_median", "warmup_s"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("cores_total", "cores_performance", "cores_efficiency", "ram_bytes", "n_clips", "peak_rss_bytes",
                "peak_gpu_bytes", "model_size_bytes"):  # fmt: skip
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def bench_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).results_dir / "compute" / "bench.parquet"


def append_bench(df: pd.DataFrame, settings: Settings | None = None) -> Path:
    path = bench_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        old = old[~old.set_index(["bench_id", "provider"]).index.isin(df.set_index(["bench_id", "provider"]).index)]
        df = pd.concat([old, df], ignore_index=True) if len(old) else df
    df.to_parquet(path, index=False)
    return path


def bench_clips(language: str, limit: int, settings: Settings | None = None, tier: str = "creator") -> list[dict]:
    """limit + 1 clips (the first is the warm up) with WAVs on disk, by clip id."""
    from clipbench.data.references import load_clips

    clips = load_clips(tier, settings=settings or get_settings(), language=language)
    clips = sorted(clips, key=lambda c: c.video_id)[: limit + 1]
    return [{"path": c.audio_path, "seconds": c.duration_s} for c in clips]


# ---------------------------------------------------------------- per run table


def latest_bench(settings: Settings | None = None) -> pd.DataFrame:
    path = bench_path(settings)
    if not path.exists():
        return pd.DataFrame(columns=BENCH_COLUMNS)
    df = pd.read_parquet(path)
    df = df[df["status"] == "ok"].sort_values("measured_at")
    return df.groupby(["provider", "model_id", "backend"], dropna=False).tail(1)


def _backend_of(provider_version: Any) -> str | None:
    text = str(provider_version or "")
    if text.startswith("mlx"):
        return "mlx"
    if text.startswith("faster-whisper"):
        return "faster-whisper"
    return None


def compute_table(run_id: str, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    rows = pd.read_parquet(s.runs_dir / run_id / "rows.parquet")
    meta_path = s.runs_dir / run_id / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    pmeta = {m.get("id"): m for m in meta.get("providers") or [] if isinstance(m, dict)}
    bench = latest_bench(s)
    records = []
    main = rows[(rows["repeat"] == 0) & (rows["status"] == "ok")]
    for provider, g in main.groupby("provider", sort=True):
        info = pmeta.get(provider, {})
        local = provider in LOCAL_PROVIDERS or (not info.get("uploads_audio", True) and not info.get("paid", False))
        live = g[~g["cached"].astype(bool) & (g["latency_ms"] > 0) & (g["audio_seconds"] > 0)]
        audio = float(live["audio_seconds"].sum())
        wall = float(live["latency_ms"].sum() / 1000)
        per = (live["audio_seconds"] / (live["latency_ms"] / 1000)).to_numpy()
        minutes = float(g["audio_seconds"].fillna(0).sum() / 60)
        price = info.get("usd_per_minute")
        model_id = ",".join(sorted({str(m) for m in g["model_id"].dropna()}))
        rec: dict[str, Any] = {
            "provider": provider, "model_id": model_id, "video_id": None, "tier": ",".join(sorted(set(g["tier"]))),
            "language": str(g["language"].iloc[0]), "kind": "local" if local else "api", "n_rows": int(len(live)),
            "audio_seconds": audio, "wall_seconds": wall, "speed_factor": audio / wall if wall > 0 else None,
            "speed_factor_median": float(np.median(per)) if len(per) else None,
            "latency_p50_ms": float(live["latency_ms"].median()) if len(live) else None,
            "usd_per_1000_min": None if local or price is None else float(price) * 1000,
            "effective_usd_per_1000_min": (float(g["cost_usd"].fillna(0).sum()) / minutes * 1000)
            if minutes > 0 and not local else None,
        }  # fmt: skip
        if local and len(bench):
            backends = {_backend_of(v) for v in g["provider_version"].dropna()}
            b = bench[bench["provider"] == provider]
            if backends - {None}:
                b = b[b["backend"].isin(backends)]
            if len(b):
                top = b.sort_values("measured_at").iloc[-1]
                for col in ("bench_id", "rtfx", "peak_rss_bytes", "peak_gpu_bytes", "model_size_bytes", "backend",
                            "machine_id", "chip", "cores_total", "ram_bytes", "os"):  # fmt: skip
                    rec[col] = None if pd.isna(top[col]) else top[col]
        records.append(rec)
    df = pd.DataFrame(records, columns=COMPUTE_COLUMNS)
    for col in ("audio_seconds", "wall_seconds", "speed_factor", "speed_factor_median", "latency_p50_ms",
                "usd_per_1000_min", "effective_usd_per_1000_min", "rtfx"):  # fmt: skip
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("peak_rss_bytes", "peak_gpu_bytes", "model_size_bytes", "cores_total", "ram_bytes"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def write_compute(run_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    compute_table(run_id, s).to_parquet(out / "compute.parquet", index=False)
    return out / "compute.parquet"


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    recs = df.astype(object).where(df.notna(), None).to_dict("records")
    out = []
    for r in recs:
        clean = {}
        for k, v in r.items():
            if isinstance(v, float) and not np.isfinite(v):
                v = None
            elif isinstance(v, np.integer):
                v = int(v)
            clean[k] = v
        out.append(clean)
    return out


def export_section(run_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """`{"compute": {"rows": [...], "machines": [...]}}`: compute.parquet of the run (or built from its rows),
    plus the bench rows of the local models it ran. None when the run has no rows."""
    s = settings or get_settings()
    path = s.results_dir / run_id / "compute.parquet"
    if path.exists():
        df = pd.read_parquet(path)
    elif (s.runs_dir / run_id / "rows.parquet").exists():
        df = compute_table(run_id, s)
    else:
        return None
    if df.empty:
        return None
    machines = df[df["machine_id"].notna()].drop_duplicates("machine_id")
    machine_cols = ["machine_id", "chip", "cores_total", "ram_bytes", "os", "backend"]
    return {
        "compute": {
            "rows": _records(df.drop(columns=["video_id"])),
            "machines": _records(machines[machine_cols]) if len(machines) else [],
        }
    }


# ---------------------------------------------------------------- CLI


def register(app) -> None:
    import typer
    from rich.console import Console
    from rich.table import Table

    capp = typer.Typer(help="Speed and compute: RTFx, memory and model size; price and speed factor.",
                       no_args_is_help=True)  # fmt: skip

    @capp.command("machine")
    def machine_cmd() -> None:
        """Print what this machine declares (chip, cores, RAM, OS, local backend)."""
        from clipbench.providers.whisper_local import detect_backend

        info = machine_info()
        info["backend"] = detect_backend()
        info["machine_id"] = machine_id(info)
        typer.echo(json.dumps(info, indent=2))

    @capp.command("bench")
    def bench_cmd(
        lang: str = typer.Option(..., "--lang"),
        providers: str = typer.Option(",".join(LOCAL_PROVIDERS), "--providers"),
        limit: int = typer.Option(10, "--limit", help="Clips to time (plus one warm up)."),
        tier: str = typer.Option("creator", "--tier", help="Which clips' audio to use: creator or verified."),
    ) -> None:
        """Time local models on this machine and append to results/compute/bench.parquet."""
        ids = [p.strip() for p in providers.split(",") if p.strip()]
        bad = [p for p in ids if p not in LOCAL_PROVIDERS]
        if bad:
            raise typer.BadParameter(f"only local models are benched here: {', '.join(LOCAL_PROVIDERS)}")
        clips = bench_clips(lang, limit, tier=tier)
        if len(clips) < 2:
            Console(stderr=True).print("[red]need at least 2 clips with audio (one is the warm up)[/red]")
            raise typer.Exit(1)
        df = bench_rows(ids, clips, lang)
        path = append_bench(df)
        table = Table(title="local model bench")
        for col in ("provider", "backend", "chip", "clips", "RTFx", "peak RSS GB", "GPU GB", "size GB", "status"):
            table.add_column(col)

        def gb(x):
            return "-" if pd.isna(x) else f"{x / 1e9:.2f}"

        for r in df.itertuples(index=False):
            table.add_row(r.provider, str(r.backend), str(r.chip), str(r.n_clips),
                          "-" if pd.isna(r.rtfx) else f"{r.rtfx:.1f}", gb(r.peak_rss_bytes), gb(r.peak_gpu_bytes),
                          gb(r.model_size_bytes), str(r.status if r.status == "ok" else r.error))  # fmt: skip
        Console().print(table)
        typer.echo(str(path))

    @capp.command("table")
    def table_cmd(run_id: str = typer.Argument(..., help="Run id under results/runs/, or 'latest'.")) -> None:
        """Write results/<run_id>/compute.parquet: speed factor, price per 1,000 minutes, bench numbers."""
        from clipbench.runs import resolve_run_id

        rid = resolve_run_id(run_id)
        path = write_compute(rid)
        df = pd.read_parquet(path)
        table = Table(title=f"compute {rid}")
        for col in ("provider", "kind", "speed factor (median)", "$/1000 min", "RTFx (bench)", "size GB"):
            table.add_column(col)
        for r in df.itertuples(index=False):
            table.add_row(r.provider, r.kind, "-" if pd.isna(r.speed_factor_median) else f"{r.speed_factor_median:.1f}",
                          "-" if pd.isna(r.usd_per_1000_min) else f"{r.usd_per_1000_min:.2f}",
                          "-" if pd.isna(r.rtfx) else f"{r.rtfx:.1f}",
                          "-" if pd.isna(r.model_size_bytes) else f"{r.model_size_bytes / 1e9:.2f}")  # fmt: skip
        Console().print(table)
        typer.echo(str(path))

    app.add_typer(capp, name="compute")


if __name__ == "__main__":
    _worker(sys.argv[1])
