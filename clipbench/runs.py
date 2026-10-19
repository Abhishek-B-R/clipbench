"""Run id helpers."""

from __future__ import annotations

from clipbench.config import Settings, get_settings


def resolve_run_id(run_id: str, settings: Settings | None = None) -> str:
    """'latest' means the most recently modified run under results/runs/."""
    if run_id != "latest":
        return run_id
    s = settings or get_settings()
    runs = [p for p in s.runs_dir.glob("*") if (p / "rows.parquet").exists()]
    if not runs:
        raise FileNotFoundError(f"no runs under {s.runs_dir}")
    return max(runs, key=lambda p: p.stat().st_mtime).name
