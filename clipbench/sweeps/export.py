"""Export hook for the site: `export_section(run_id, settings) -> {"sweeps": ...} | None`.

A sweep is not tied to one leaderboard run, so the section lists every scored sweep in the run's
language (results/sweeps/*/sweeps.parquet), newest first. Numbers, ids and bed licenses only.

Shape:

    {"sweeps": [{"sweep_id", "language", "created_at", "snrs_db", "bed_kinds", "reverb", "rt60_s",
                 "pad_s", "n_clips", "beds": [{id, kind, source, page_url, license, license_url,
                 attribution}], "rows": [<sweeps.parquet rows for the primary metric>]}]}
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from clipbench.config import Settings, get_settings


def run_language(run_id: str, settings: Settings) -> str | None:
    meta = settings.runs_dir / run_id / "run_meta.json"
    if meta.is_file():
        try:
            lang = json.loads(meta.read_text()).get("language")
            if lang:
                return str(lang).split("-")[0].lower()
        except ValueError:
            pass
    rows = settings.runs_dir / run_id / "rows.parquet"
    if rows.is_file():
        langs = pd.read_parquet(rows, columns=["language"])["language"].dropna()
        if len(langs):
            return str(langs.iloc[0]).split("-")[0].lower()
    return None


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for rec in df.astype(object).where(df.notna(), None).to_dict("records"):
        out.append({k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for k, v in rec.items()})
    return out


def sweeps_for_language(language: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    root = s.results_dir / "sweeps"
    found: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/sweeps.parquet")) if root.is_dir() else []:
        sweep_dir: Path = path.parent
        meta_path = sweep_dir / "sweep_meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        if str(meta.get("language", "")).split("-")[0].lower() != language:
            continue
        df = pd.read_parquet(path)
        if "is_primary" in df:
            df = df[df["is_primary"].astype(bool)]
        found.append(
            {
                "sweep_id": meta.get("sweep_id", sweep_dir.name),
                "language": language,
                "created_at": meta.get("created_at"),
                "snrs_db": meta.get("snrs_db"),
                "bed_kinds": meta.get("bed_kinds"),
                "reverb": meta.get("reverb"),
                "rt60_s": meta.get("rt60_s"),
                "pad_s": meta.get("pad_s"),
                "n_clips": meta.get("n_clips"),
                "beds": meta.get("beds", []),
                "rows": _records(df),
            }
        )
    found.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return found


def export_section(run_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    s = settings or get_settings()
    lang = run_language(run_id, s)
    if not lang:
        return None
    sweeps = sweeps_for_language(lang, s)
    return {"sweeps": sweeps} if sweeps else None
