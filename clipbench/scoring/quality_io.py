"""Shared inputs and helpers for the v3 quality metrics (formatting, entities, style, semantic).

Reads a run's numeric rows, hypothesis texts and reference snapshot. The v3 reference fields
(`entity_spans`, `alternatives`, `itn_spans`, `conditions`) come from refs.parquet columns
`<field>_json` when the runner wrote them, else from `data/verified/<clip_id>.json` for verified clips.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings
from clipbench.models import CATEGORIES, CONDITIONS, AlternativeSpan, EntitySpan, ItnSpan, NonSpeechSpan
from clipbench.scoring.stats import bootstrap_ci


def missing(value: Any) -> bool:
    return value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value))


def _load_json(value: Any) -> Any:
    if missing(value) or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    if isinstance(value, np.ndarray):
        return list(value)
    return value


def _models(value: Any, model: type) -> list:
    parsed = _load_json(value)
    if not isinstance(parsed, list):
        return []
    out = []
    for x in parsed:
        try:
            out.append(model.model_validate(x))
        except ValueError:
            continue
    return out


@dataclass
class QualityRef:
    video_id: str
    tier: str
    text: str
    punctuated: bool = False
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    entity_spans: list[EntitySpan] = field(default_factory=list)
    alternatives: list[AlternativeSpan] = field(default_factory=list)
    itn_spans: list[ItnSpan] = field(default_factory=list)
    nonspeech: list[NonSpeechSpan] = field(default_factory=list)

    @property
    def all_conditions(self) -> list[str]:
        """Conditions with `music` implied by the background_music tag or a music non-speech span."""
        out = [c for c in self.conditions if c in CONDITIONS]
        if "music" not in out and ("background_music" in self.tags or any(s.kind == "music" for s in self.nonspeech)):
            out.append("music")
        return out


@dataclass
class RunInputs:
    run_id: str
    language: str
    rows: pd.DataFrame  # repeat 0, status ok
    texts: dict[tuple[str, str], str]  # (provider, video_id) -> hypothesis text of repeat 0
    refs: dict[tuple[str, str], QualityRef]  # (video_id, tier) -> reference

    def pairs(self) -> Iterator[tuple[Any, QualityRef, str]]:
        """(row, reference, hypothesis text) for every scored row with a reference."""
        for row in self.rows.itertuples(index=False):
            ref = self.refs.get((row.video_id, row.tier))
            if ref is None:
                continue
            yield row, ref, self.texts.get((row.provider, row.video_id), "")


def _verified_file(settings: Settings, video_id: str) -> dict[str, Any]:
    path = settings.verified_dir / f"{video_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _ref_from_row(r: Any, settings: Settings) -> QualityRef:
    def col(name: str) -> Any:
        value = getattr(r, name, None)
        return None if missing(value) else value

    verified = _verified_file(settings, r.video_id) if r.tier == "verified" else {}

    def v3(name: str, model: type | None) -> list:
        raw = col(f"{name}_json")
        if raw is None:
            raw = verified.get(name)
        if model is None:
            parsed = _load_json(raw)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        return _models(raw, model)

    tags = _load_json(col("tags"))
    punct = col("punctuated")
    return QualityRef(
        video_id=r.video_id,
        tier=r.tier,
        text="" if col("ref_text") is None else str(col("ref_text")),
        punctuated=str(punct).strip().lower() in ("true", "1", "yes") if punct is not None else False,
        category=None if col("category") is None else str(col("category")),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        conditions=v3("conditions", None),
        entity_spans=v3("entity_spans", EntitySpan),
        alternatives=v3("alternatives", AlternativeSpan),
        itn_spans=v3("itn_spans", ItnSpan),
        nonspeech=_models(col("nonspeech_spans_json"), NonSpeechSpan),
    )


def load_run(run_id: str, settings: Settings) -> RunInputs | None:
    """None when the run's private inputs (texts, refs) are not on this machine."""
    rows_path = settings.runs_dir / run_id / "rows.parquet"
    texts_path = settings.runs_text_dir / run_id / "texts.parquet"
    refs_path = settings.runs_text_dir / run_id / "refs.parquet"
    if not (rows_path.exists() and texts_path.exists() and refs_path.exists()):
        return None
    rows = pd.read_parquet(rows_path)
    rows = rows[(rows["repeat"] == 0) & (rows["status"] == "ok")].reset_index(drop=True)
    texts_df = pd.read_parquet(texts_path)
    texts = {
        (t.provider, t.video_id): "" if missing(t.hyp_text) else str(t.hyp_text)
        for t in texts_df.itertuples(index=False)
        if int(t.repeat) == 0
    }
    refs = {(r.video_id, r.tier): _ref_from_row(r, settings) for r in pd.read_parquet(refs_path).itertuples(index=False)}
    langs = sorted({str(x).split("-")[0].lower() for x in rows["language"].dropna()})
    return RunInputs(run_id, langs[0] if langs else "", rows, texts, refs)


def results_path(settings: Settings, run_id: str, name: str) -> Path:
    return settings.results_dir / run_id / name


# ---------------------------------------------------------------------------
# Slices and aggregates
# ---------------------------------------------------------------------------


def slices(df: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    """(slice_kind, slice, mask): all, each condition (docs/V3.md) and each category present.

    Needs a `conditions` column (json list string) and a `category` column."""
    out: list[tuple[str, str, pd.Series]] = [("all", "all", pd.Series(True, index=df.index))]
    if "conditions" in df:
        conds = df["conditions"].map(lambda v: _load_json(v) or [])
        for c in CONDITIONS:
            mask = conds.map(lambda xs, c=c: c in xs)
            if mask.any():
                out.append(("condition", c, mask))
    if "category" in df:
        for c in CATEGORIES:
            mask = df["category"] == c
            if mask.any():
                out.append(("category", c, mask))
    return out


def rate_ci(num: Any, den: Any, seed: int) -> tuple[float, float, float]:
    """Corpus rate sum(num) / sum(den) over clips with a clip bootstrap CI. NaN when den sums to 0."""
    n = np.asarray(num, dtype=float)
    d = np.asarray(den, dtype=float)
    keep = ~(np.isnan(n) | np.isnan(d))
    n, d = n[keep], d[keep]
    if len(n) == 0 or d.sum() <= 0:
        return (np.nan, np.nan, np.nan)
    ci = bootstrap_ci(n, d, seed=seed)
    return (ci.rate, ci.ci_low, ci.ci_high)


def model_id(group: pd.DataFrame) -> str:
    return ",".join(sorted({str(m) for m in group["model_id"].dropna()}))


def grouped(
    df: pd.DataFrame, metrics: Callable[[pd.DataFrame], dict[str, Any]], extra_keys: list[str] | None = None
) -> pd.DataFrame:
    """Aggregate rows per (provider, tier, language, slice, *extra_keys) with `metrics(group)`."""
    keys = ["provider", "tier", "language", *(extra_keys or [])]
    records = []
    if df.empty:
        return pd.DataFrame()
    for kind, name, mask in slices(df):
        part = df[mask]
        for key, group in part.groupby(keys, sort=True, dropna=False):
            key = key if isinstance(key, tuple) else (key,)
            records.append(
                {
                    **dict(zip(keys, key, strict=True)),
                    "model_id": model_id(group),
                    "slice_kind": kind,
                    "slice": name,
                    "n_clips": int(group["video_id"].nunique()),
                    **metrics(group),
                }
            )
    return pd.DataFrame(records)


def frame_records(df: pd.DataFrame | None) -> list[dict[str, Any]] | None:
    if df is None or df.empty:
        return None
    out = df.replace({np.nan: None}).to_dict(orient="records")
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()} for r in out]
