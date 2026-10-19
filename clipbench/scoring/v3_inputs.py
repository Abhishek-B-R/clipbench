"""Shared run loading for the v3 provider metrics (diarization, LID, captions).

Reads the same files as score.py: `results/runs/<run_id>/rows.parquet` (numbers),
`data/runs/<run_id>/texts.parquet` and `refs.parquet` (text, never published).

Two v3 inputs are not in the v2 snapshots yet, so they are read with fallbacks:
- per word speaker labels: a `speakers_json` column in texts.parquet (a JSON list parallel to
  words_json), else a `speaker` key on each words_json entry;
- reference `speaker_turns` and `conditions`: `speaker_turns_json` / `conditions_json` columns in
  refs.parquet, else the verified file `data/verified/<clip_id>.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from clipbench.config import Settings
from clipbench.models import Reference, SpeakerTurn, Word

TOO_SMALL = 15  # same threshold as score.py: slices with fewer clips are flagged too_small


def missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _json(value: Any) -> Any:
    if missing(value):
        return None
    if isinstance(value, list | dict):
        return value
    try:
        return json.loads(str(value))
    except ValueError:
        return None


@dataclass
class Hyp:
    text: str
    words: list[Word] | None
    speakers: list[str | None] | None


@dataclass
class Ref:
    video_id: str
    tier: str
    text: str
    words: list[Word] | None
    speaker_turns: list[SpeakerTurn] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class RunInputs:
    run_id: str
    rows: pd.DataFrame  # every row; use main_rows() for scored ones
    hyps: dict[tuple[str, str, int], Hyp]
    refs: dict[tuple[str, str], Ref]

    def main_rows(self) -> pd.DataFrame:
        r = self.rows
        return r[(r["repeat"] == 0) & (r["status"] == "ok")]

    def hyp(self, provider: str, video_id: str) -> Hyp | None:
        return self.hyps.get((provider, video_id, 0))

    def ref(self, video_id: str, tier: str) -> Ref | None:
        return self.refs.get((video_id, tier))


def parse_words(value: Any) -> list[Word] | None:
    data = _json(value)
    if data is None:
        return None
    return [Word(text=str(w.get("text", "")), start_ms=int(w["start_ms"]), end_ms=int(w["end_ms"])) for w in data]


def parse_speakers(speakers_value: Any, words_value: Any) -> list[str | None] | None:
    data = _json(speakers_value)
    if isinstance(data, list):
        return [None if s is None else str(s) for s in data]
    words = _json(words_value)
    if isinstance(words, list) and any(isinstance(w, dict) and w.get("speaker") is not None for w in words):
        return [None if w.get("speaker") is None else str(w["speaker"]) for w in words]
    return None


def row_options(value: Any) -> dict[str, Any]:
    data = _json(value)
    return data if isinstance(data, dict) else {}


def _verified(settings: Settings, video_id: str) -> Reference | None:
    path = settings.verified_dir / f"{video_id}.json"
    if not path.exists():
        return None
    try:
        return Reference.model_validate_json(path.read_text())
    except ValueError:
        return None


def _read(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def load_run(run_id: str, settings: Settings) -> RunInputs | None:
    """None when the run has no rows."""
    rows = _read(settings.runs_dir / run_id / "rows.parquet")
    if rows is None or rows.empty:
        return None
    texts = _read(settings.runs_text_dir / run_id / "texts.parquet")
    refs_df = _read(settings.runs_text_dir / run_id / "refs.parquet")
    hyps: dict[tuple[str, str, int], Hyp] = {}
    if texts is not None:
        has_sp = "speakers_json" in texts.columns
        for t in texts.to_dict("records"):
            hyps[(str(t["provider"]), str(t["video_id"]), int(t["repeat"]))] = Hyp(
                text="" if missing(t.get("hyp_text")) else str(t["hyp_text"]),
                words=parse_words(t.get("words_json")),
                speakers=parse_speakers(t.get("speakers_json") if has_sp else None, t.get("words_json")),
            )
    refs: dict[tuple[str, str], Ref] = {}
    if refs_df is not None:
        for r in refs_df.to_dict("records"):
            video_id, tier = str(r["video_id"]), str(r["tier"])
            turns_raw = _json(r.get("speaker_turns_json"))
            conds_raw = _json(r.get("conditions_json"))
            verified = None
            if turns_raw is None or conds_raw is None:
                verified = _verified(settings, video_id) if tier == "verified" else None
            turns = (
                [SpeakerTurn.model_validate(x) for x in turns_raw]
                if isinstance(turns_raw, list)
                else (list(verified.speaker_turns) if verified else [])
            )
            conds = (
                [str(x) for x in conds_raw]
                if isinstance(conds_raw, list)
                else (list(verified.conditions) if verified else [])
            )
            tags = _json(r.get("tags"))
            refs[(video_id, tier)] = Ref(
                video_id=video_id,
                tier=tier,
                text="" if missing(r.get("ref_text")) else str(r["ref_text"]),
                words=parse_words(r.get("words_json")),
                speaker_turns=turns,
                conditions=conds,
                tags=[str(x) for x in tags] if isinstance(tags, list) else [],
            )
    return RunInputs(run_id, rows, hyps, refs)


def model_ids(group: pd.DataFrame) -> str:
    return ",".join(sorted({str(m) for m in group["model_id"].dropna()}))


def slices(df: pd.DataFrame, conditions_col: str = "conditions") -> list[tuple[str, str, pd.Series]]:
    """('all', 'all') plus one ('condition', name) slice per condition present (JSON list column)."""
    out: list[tuple[str, str, pd.Series]] = [("all", "all", pd.Series(True, index=df.index))]
    if conditions_col in df.columns and len(df):
        lists = df[conditions_col].map(lambda v: _json(v) or [])
        for name in sorted({c for cs in lists for c in cs}):
            out.append(("condition", name, lists.map(lambda cs, n=name: n in cs)))
    return out


def records(df: pd.DataFrame | None) -> list[dict[str, Any]] | None:
    """JSON safe records (NaN -> None), or None for an empty frame."""
    if df is None or df.empty:
        return None
    clean = df.astype(object).where(df.notna(), None)
    return clean.to_dict("records")
