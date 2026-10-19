"""Write minimal run files (rows, texts, refs) for the v3 provider metric tests."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from clipbench.config import Settings


def words(*items: tuple[str, int, int]) -> list[dict[str, Any]]:
    return [{"text": t, "start_ms": s, "end_ms": e} for t, s, e in items]


def write_run(
    settings: Settings,
    run_id: str,
    hyps: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    *,
    language: str = "en",
    options: dict[str, Any] | None = None,
) -> None:
    """hyps: {provider, video_id, text, words, speakers?, detected_language?, status?, model_id?}.
    refs: {video_id, text, words?, speaker_turns?, conditions?, tier?}."""
    rows, texts = [], []
    for h in hyps:
        rows.append(
            {
                "run_id": run_id,
                "provider": h["provider"],
                "model_id": h.get("model_id", f"{h['provider']}-m"),
                "video_id": h["video_id"],
                "language": language,
                "tier": h.get("tier", "verified"),
                "repeat": 0,
                "status": h.get("status", "ok"),
                "detected_language": h.get("detected_language"),
                "options": json.dumps(h.get("options", options or {}), sort_keys=True),
                "duration_s": h.get("duration_s", 10.0),
                "audio_seconds": h.get("duration_s", 10.0),
            }
        )
        texts.append(
            {
                "provider": h["provider"],
                "video_id": h["video_id"],
                "repeat": 0,
                "hyp_text": h.get("text", ""),
                "words_json": None if h.get("words") is None else json.dumps(h["words"]),
                "speakers_json": None if h.get("speakers") is None else json.dumps(h["speakers"]),
            }
        )
    ref_rows = [
        {
            "video_id": r["video_id"],
            "tier": r.get("tier", "verified"),
            "ref_text": r.get("text", ""),
            "words_json": None if r.get("words") is None else json.dumps(r["words"]),
            "tags": json.dumps(r.get("tags", [])),
            "speaker_turns_json": json.dumps(r.get("speaker_turns", [])),
            "conditions_json": json.dumps(r.get("conditions", [])),
        }
        for r in refs
    ]
    rows_dir = settings.runs_dir / run_id
    text_dir = settings.runs_text_dir / run_id
    rows_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(rows_dir / "rows.parquet", index=False)
    pd.DataFrame(texts).to_parquet(text_dir / "texts.parquet", index=False)
    pd.DataFrame(ref_rows).to_parquet(text_dir / "refs.parquet", index=False)
