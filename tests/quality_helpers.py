"""Shared fixtures for the v3 quality metric tests: a tiny run on disk."""

from __future__ import annotations

import json

import pandas as pd


def write_run(settings, run_id: str, language: str, clips: list[dict], hyps: dict[str, list[str]],
              options: dict[str, dict] | None = None, tier: str = "verified") -> None:
    """clips: [{"text", optional "punctuated", "itn_spans", "entity_spans", "conditions", "category"}].
    hyps: provider -> hypothesis text per clip."""
    rows, texts, refs = [], [], []
    for i, c in enumerate(clips):
        vid = c.get("video_id", f"v{i:02d}")
        refs.append(
            {
                "video_id": vid, "tier": tier, "ref_text": c["text"], "words_json": None,
                "tags": json.dumps(c.get("tags", [])), "category": c.get("category"),
                "punctuated": "true" if c.get("punctuated") else "false",
                "itn_spans_json": json.dumps(c.get("itn_spans", [])),
                "entity_spans_json": json.dumps(c.get("entity_spans", [])),
                "conditions_json": json.dumps(c.get("conditions", [])),
            }
        )  # fmt: skip
        for provider, texts_ in hyps.items():
            rows.append(
                {
                    "run_id": run_id, "provider": provider, "model_id": f"{provider}-1", "video_id": vid,
                    "language": language, "tier": tier, "repeat": 0, "status": "ok",
                    "audio_seconds": c.get("seconds", 60.0), "duration_s": c.get("seconds", 60.0),
                    "latency_ms": 100.0, "cost_usd": 0.0,
                    "options": json.dumps((options or {}).get(provider, {})),
                }
            )  # fmt: skip
            texts.append({"provider": provider, "video_id": vid, "repeat": 0, "hyp_text": texts_[i], "words_json": None})
    (settings.runs_dir / run_id).mkdir(parents=True, exist_ok=True)
    (settings.runs_text_dir / run_id).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(settings.runs_dir / run_id / "rows.parquet")
    pd.DataFrame(texts).to_parquet(settings.runs_text_dir / run_id / "texts.parquet")
    pd.DataFrame(refs).to_parquet(settings.runs_text_dir / run_id / "refs.parquet")
