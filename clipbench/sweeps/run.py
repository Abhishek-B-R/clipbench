"""`clipbench sweep run <sweep_id>`: batch providers over every variant of a sweep.

One sweep is one runner run (run id = sweep id) holding every SNR level, with clip ids
`<clip_id>#<condition>`. One run keeps resume, the budget guard, the cache and run_meta in one place, and
the score can pair the conditions of a clip directly. The run is written next to the sweep instead of
under results/runs/, so it never shows up as a leaderboard run:

- results/sweeps/<sweep_id>/rows.parquet, run_meta.json   (numbers only, run_meta.kind = "sweep")
- data/sweeps/<sweep_id>/texts.parquet, refs.parquet       (text, never published)

Stability reruns are off: a sweep already multiplies the audio per clip by the number of conditions.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from clipbench.config import Settings, get_settings
from clipbench.sweeps.build import load_variant_clips


@dataclass(frozen=True)
class SweepSettings(Settings):
    """Settings whose run directories point at the sweep folders (results/sweeps, data/sweeps)."""

    @property
    def runs_dir(self) -> Path:  # type: ignore[override]
        return self.results_dir / "sweeps"

    @property
    def runs_text_dir(self) -> Path:  # type: ignore[override]
        return self.data_dir / "sweeps"


def sweep_settings(settings: Settings | None = None) -> SweepSettings:
    s = settings or get_settings()
    return SweepSettings(**{f.name: getattr(s, f.name) for f in dataclasses.fields(Settings)})


async def run_sweep(
    sweep_id: str,
    providers: list[Any],
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    no_cache: bool = False,
    limit_clips: int | None = None,
    console: Console | None = None,
    progress: bool = True,
):
    """Run `providers` (ids or Provider instances) over the sweep's variants. Returns the RunResult."""
    from clipbench.runner import run

    s = settings or get_settings()
    clips = load_variant_clips(sweep_id, s)
    if limit_clips is not None:
        keep = sorted({c.video_id.split("#")[0] for c in clips})[:limit_clips]
        clips = [c for c in clips if c.video_id.split("#")[0] in set(keep)]
    missing = [c.video_id for c in clips if not Path(c.audio_path).is_file()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} mixed WAVs are missing (first: {missing[0]}); rebuild the sweep")
    ss = sweep_settings(s)
    result = await run(
        providers,
        "verified",
        clips=clips,
        run_id=sweep_id,
        no_cache=no_cache,
        dry_run=dry_run,
        stability=False,
        settings=ss,
        console=console,
        progress=progress,
        seed=s.sample_seed,
    )
    if not dry_run and result.meta_path is not None:
        meta = json.loads(result.meta_path.read_text())
        meta["kind"] = "sweep"
        meta["sweep_id"] = sweep_id
        result.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str))
    return result
