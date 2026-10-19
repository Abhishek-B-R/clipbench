"""B20 feature matrix, read from the `[<id>.features]` tables in pricing.toml.

Each feature has a value plus `<key>_url` and `<key>_checked_on` (and an optional `<key>_note`).
A value left out of the table means unknown or not stated by the provider, exported as null.
"""

from __future__ import annotations

from typing import Any

from clipbench.providers.base import load_pricing
from clipbench.providers.registry import REGISTRY

FEATURE_KEYS = [
    "languages",
    "batch",
    "streaming",
    "word_timestamps",
    "diarization",
    "language_id",
    "custom_vocabulary",
    "max_file_minutes",
    "retention",
    "training_opt_out",
]


def provider_features(provider_id: str) -> dict[str, Any] | None:
    """{key: {"value", "url", "checked_on", "note"}} plus price, or None without a features table."""
    entry = load_pricing().get(provider_id) or {}
    table = entry.get("features")
    if not isinstance(table, dict):
        return None
    cells = {
        key: {
            "value": table.get(key),
            "url": table.get(f"{key}_url"),
            "checked_on": table.get(f"{key}_checked_on"),
            "note": table.get(f"{key}_note"),
        }
        for key in FEATURE_KEYS
    }
    return {
        "provider": provider_id,
        "model_id": entry.get("model_id"),
        "usd_per_minute": entry.get("usd_per_minute"),
        "price_url": entry.get("checked_url"),
        "price_checked_on": entry.get("checked_on"),
        "features": cells,
    }


def feature_matrix() -> list[dict[str, Any]]:
    """One record per registered provider that has a features table, in registry order."""
    out = []
    for pid in REGISTRY:
        rec = provider_features(pid)
        if rec is not None:
            out.append(rec)
    return out


def missing_cells() -> list[str]:
    """'<provider>.<key>' for every cell without a url or checked_on date (a publish check)."""
    bad = []
    for rec in feature_matrix():
        for key, cell in rec["features"].items():
            if not cell["url"] or not cell["checked_on"]:
                bad.append(f"{rec['provider']}.{key}")
    return bad
