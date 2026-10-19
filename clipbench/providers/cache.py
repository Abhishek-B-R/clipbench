"""On-disk result cache so reruns never bill twice.

Key: (provider id, model id, audio sha256, settings hash). One JSON file per key under
data/cache/<provider>/<model_id>/<sha256>_<settings_hash>.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from clipbench.models import Result


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", part)


class ResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, provider_id: str, model_id: str, audio_sha256: str, settings_hash: str) -> Path:
        return self.root / _safe(provider_id) / _safe(model_id) / f"{audio_sha256}_{settings_hash}.json"

    def get(self, provider_id: str, model_id: str, audio_sha256: str, settings_hash: str) -> Result | None:
        p = self.path(provider_id, model_id, audio_sha256, settings_hash)
        if not p.exists():
            return None
        try:
            return Result.model_validate_json(p.read_text())
        except ValueError:
            return None

    def put(self, provider_id: str, model_id: str, audio_sha256: str, settings_hash: str, result: Result) -> Path:
        p = self.path(provider_id, model_id, audio_sha256, settings_hash)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        tmp.replace(p)
        return p
