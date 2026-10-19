"""API reliability (B19): failure rate, timeouts, retries, latency tail and rate limiting per provider.

Inputs are the numeric rows every run writes (`rows.parquet`, docs/V3.md): `status, timed_out, retries,
http_status, latency_ms, cached`. Only requests that went out count: cached rows (served from the result
cache, no request) and skipped rows (budget stop, no data for the clip) are left out.

- failure_rate: errors / requests, with a 95% Wilson interval.
- timeout_rate: rows the runner gave up on after the job timeout / requests.
- mean_retries: retries the adapter made per request (after 429, 5xx or network errors).
- latency_p50/p95/p99: milliseconds over successful requests.
- rate_limited_share: requests whose last HTTP status was 429 / requests.

Outputs:

- `results/<run_id>/reliability.parquet`: one row per provider for that run.
- `results/history/reliability.parquet`: one row per provider across every run under `results/runs/`
  and the canary runs under `results/canary/runs/`.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from clipbench.config import Settings, get_settings

RELIABILITY_COLUMNS = [
    "provider", "model_id", "tier", "language", "slice_kind", "slice", "n_runs", "n_clips", "n_requests",
    "n_ok", "n_errors", "n_timeouts", "n_rate_limited", "failure_rate", "failure_ci_low", "failure_ci_high",
    "timeout_rate", "mean_retries", "max_retries", "latency_p50", "latency_p95", "latency_p99",
    "rate_limited_share", "first_run", "last_run",
]  # fmt: skip


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def requests_only(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows that were real requests: status ok or error, and not served from the cache."""
    if rows.empty:
        return rows
    df = rows[rows["status"].isin(["ok", "error"])]
    if "cached" in df:
        df = df[~df["cached"].fillna(False).astype(bool)]
    return df


def _col(df: pd.DataFrame, name: str, default: Any) -> pd.Series:
    return df[name] if name in df else pd.Series([default] * len(df), index=df.index)


def _summary(group: pd.DataFrame) -> dict[str, Any]:
    n = len(group)
    errors = int((group["status"] == "error").sum())
    timeouts = int(_col(group, "timed_out", False).fillna(False).astype(bool).sum())
    http = pd.to_numeric(_col(group, "http_status", None), errors="coerce")
    limited = int((http == 429).sum())
    retries = pd.to_numeric(_col(group, "retries", 0), errors="coerce").fillna(0)
    ok_lat = pd.to_numeric(group.loc[group["status"] == "ok", "latency_ms"], errors="coerce").dropna()
    lo, hi = wilson(errors, n)

    def q(p: float) -> float:
        return float(np.quantile(ok_lat, p)) if len(ok_lat) else float("nan")

    runs = sorted(str(r) for r in _col(group, "run_id", None).dropna().unique())
    return {
        "n_runs": len(runs),
        "n_clips": int(group["video_id"].nunique()) if "video_id" in group else 0,
        "n_requests": n,
        "n_ok": n - errors,
        "n_errors": errors,
        "n_timeouts": timeouts,
        "n_rate_limited": limited,
        "failure_rate": errors / n if n else float("nan"),
        "failure_ci_low": lo,
        "failure_ci_high": hi,
        "timeout_rate": timeouts / n if n else float("nan"),
        "mean_retries": float(retries.mean()) if n else float("nan"),
        "max_retries": int(retries.max()) if n else 0,
        "latency_p50": q(0.50),
        "latency_p95": q(0.95),
        "latency_p99": q(0.99),
        "rate_limited_share": limited / n if n else float("nan"),
        "first_run": runs[0] if runs else None,
        "last_run": runs[-1] if runs else None,
    }


def _model_id(group: pd.DataFrame) -> str | None:
    vals = _col(group, "model_id", None).dropna().astype(str)
    return vals.mode().iloc[0] if len(vals) else None


def _one(values: pd.Series) -> str | None:
    vals = sorted({str(v) for v in values.dropna()})
    return vals[0] if len(vals) == 1 else ("mixed" if vals else None)


def reliability(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per provider over `rows` (any number of runs)."""
    df = requests_only(rows)
    records = []
    for provider, group in df.groupby("provider", sort=True):
        records.append(
            {
                "provider": str(provider),
                "model_id": _model_id(group),
                "tier": _one(_col(group, "tier", None)),
                "language": _one(_col(group, "language", None)),
                "slice_kind": "all",
                "slice": "all",
                **_summary(group),
            }
        )
    out = pd.DataFrame(records, columns=RELIABILITY_COLUMNS)
    for col in ("provider", "model_id", "tier", "language", "slice_kind", "slice", "first_run", "last_run"):
        out[col] = out[col].astype("string")
    return out


def _atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _read_rows(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - an unreadable or partial run is skipped
        return None


def run_dirs(settings: Settings) -> list[Path]:
    """Every run with rows: benchmark runs and canary runs."""
    roots = [settings.runs_dir, settings.results_dir / "canary" / "runs"]
    return sorted(p.parent for root in roots for p in root.glob("*/rows.parquet"))


def write_run_reliability(run_id: str, settings: Settings, *, runs_dir: Path | None = None,
                          out_dir: Path | None = None) -> Path | None:  # fmt: skip
    """results/<run_id>/reliability.parquet for one run. None when the run has no rows."""
    rows = _read_rows((runs_dir or settings.runs_dir) / run_id / "rows.parquet")
    if rows is None:
        return None
    path = (out_dir or settings.results_dir / run_id) / "reliability.parquet"
    _atomic_parquet(reliability(rows), path)
    return path


def write_history(settings: Settings) -> Path:
    """results/history/reliability.parquet across every run (benchmark and canary)."""
    frames = [df for d in run_dirs(settings) if (df := _read_rows(d / "rows.parquet")) is not None and len(df)]
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["provider", "status"])
    out = reliability(rows)
    out["tier"] = pd.Series([None] * len(out), dtype="string")  # across runs: every tier and language
    out["language"] = pd.Series([None] * len(out), dtype="string")
    path = settings.results_dir / "history" / "reliability.parquet"
    _atomic_parquet(out, path)
    return path


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for r in df.astype(object).to_dict("records"):
        clean = {}
        for k, v in r.items():
            if v is None or v is pd.NA or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                clean[k] = None
            elif hasattr(v, "item"):
                clean[k] = v.item()
            else:
                clean[k] = v
        out.append(clean)
    return out


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    """`reliability`: {"run": per provider for this run, "all_runs": per provider across every run}.

    Refreshes results/<run_id>/reliability.parquet and results/history/reliability.parquet on the way."""
    rows = _read_rows(settings.runs_dir / run_id / "rows.parquet")
    run = None
    if rows is not None:
        run = reliability(rows)
        _atomic_parquet(run, settings.results_dir / run_id / "reliability.parquet")
    hist = None
    if run_dirs(settings):
        hist = _read_rows(write_history(settings))
    if run is None and hist is None:
        return None
    return {
        "reliability": {
            "run": [] if run is None else _records(run),
            "all_runs": [] if hist is None else _records(hist),
        }
    }


def register(app: typer.Typer) -> None:
    @app.command("reliability")
    def reliability_cmd(
        run_id: str = typer.Argument("all", help="A run id, 'latest', or 'all' for every run."),
    ) -> None:
        """Write reliability.parquet (per run) and results/history/reliability.parquet (all runs)."""
        from clipbench.runs import resolve_run_id

        settings = get_settings()
        if run_id == "all":
            ids = [d.name for d in settings.runs_dir.glob("*") if (d / "rows.parquet").exists()]
        else:
            try:
                ids = [resolve_run_id(run_id, settings)]
            except FileNotFoundError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
        for one in sorted(ids):
            path = write_run_reliability(one, settings)
            if path is not None:
                typer.echo(f"wrote {path}")
        hist = write_history(settings)
        df = pd.read_parquet(hist)
        typer.echo(f"wrote {hist}")
        for r in df.itertuples():
            typer.echo(
                f"{r.provider}: {r.n_requests} requests, failures {r.failure_rate:.1%}, timeouts "
                f"{r.timeout_rate:.1%}, retries {r.mean_retries:.2f}, p99 {r.latency_p99:.0f} ms, "
                f"429 {r.rate_limited_share:.1%}"
            )
        if df.empty:
            typer.echo("no requests in any run yet")
