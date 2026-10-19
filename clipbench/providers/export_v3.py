"""Export hook for the B20 feature matrix (loaded by clipbench.report.export.V3_HOOKS).

`export_section(run_id, settings)` returns `{"features": [...]}`, the capability matrix from
pricing.toml (the same for every run). The run level sections `diarization`, `lid` and `captions`
come from the hooks in clipbench.scoring.{diarization,lid,captions}, also listed in V3_HOOKS.
"""

from __future__ import annotations

from typing import Any

from clipbench.config import Settings
from clipbench.providers.features import feature_matrix


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    matrix = feature_matrix()
    return {"features": matrix} if matrix else None
