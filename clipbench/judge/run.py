"""Judge a run (semantic.parquet), collect human labels, and calibrate the judge against them.

Human labels live in data/calibration/semantic_pairs.csv (gitignored, they hold caption text). Only the
agreement statistics (judge_calibration.json) are ever published. Nothing here invents a label.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from clipbench.budget import BudgetGuard, check_can_start, spent_usd
from clipbench.config import Settings
from clipbench.judge.client import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    JudgeError,
    SemanticJudge,
    credentials_present,
    estimate_pair_usd,
    pair_id,
)
from clipbench.providers.cache import ResultCache
from clipbench.scoring.quality_io import load_run, missing

LABEL_COLUMNS = [
    "lang", "ref", "hyp", "human_score", "labeller", "note", "pair_id", "run_id", "provider", "video_id",
    "labelled_at",
]  # fmt: skip
# Publishing gate, see METHODOLOGY.md.
MIN_PAIRS = 50
MIN_SPEARMAN = 0.7
MAX_MAE = 0.2


def labels_path(settings: Settings) -> Path:
    return settings.data_dir / "calibration" / "semantic_pairs.csv"


def calibration_path(settings: Settings) -> Path:
    return settings.data_dir / "calibration" / "judge_calibration.json"


# ---------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------


@dataclass
class Pair:
    run_id: str
    provider: str
    model_id: str
    video_id: str
    tier: str
    language: str
    ref: str
    hyp: str
    category: str | None
    conditions: str

    @property
    def id(self) -> str:
        return pair_id(self.ref, self.hyp)


def run_pairs(run_id: str, settings: Settings) -> list[Pair] | None:
    """Verified tier rows of a run: the judge compares against the verified reference only."""
    data = load_run(run_id, settings)
    if data is None:
        return None
    out = []
    for row, ref, hyp in data.pairs():
        if ref.tier != "verified":
            continue
        out.append(
            Pair(run_id, row.provider, str(row.model_id), row.video_id, row.tier, row.language, ref.text, hyp,
                 ref.category, json.dumps(ref.all_conditions))  # fmt: skip
        )
    return out


@dataclass
class Plan:
    n_pairs: int
    n_unique: int
    n_cached: int
    est_usd: float
    spent_usd: float
    cap_usd: float

    @property
    def fits(self) -> bool:
        return self.est_usd + self.spent_usd <= self.cap_usd + 1e-12

    def describe(self, model: str, prompt: str) -> str:
        return (
            f"judge {model} prompt {prompt}: {self.n_pairs} pairs, {self.n_unique} unique, {self.n_cached} cached, "
            f"{self.n_unique - self.n_cached} to request, estimated ${self.est_usd:.4f} "
            f"(spent ${self.spent_usd:.4f} of the ${self.cap_usd:.2f} cap)"
        )


def plan(pairs: list[tuple[str, str]], judge: SemanticJudge, settings: Settings) -> Plan:
    unique = {pair_id(r, h): (r, h) for r, h in pairs}
    to_do = {k: v for k, v in unique.items() if judge.cached(*v) is None}
    est = sum(estimate_pair_usd(judge.model, r, h) for r, h in to_do.values())
    return Plan(len(pairs), len(unique), len(unique) - len(to_do), est, spent_usd(settings.spend_path),
                settings.budget_usd)  # fmt: skip


async def _score_all(
    judge: SemanticJudge, items: list[tuple[str, str, str, str]]
) -> dict[str, tuple[float | None, str | None, float, bool, str | None]]:
    """items: (pair id, language, ref, hyp). -> pair id: (score, reason, cost, cached, error)."""
    out: dict[str, Any] = {}

    async def one(pid: str, lang: str, ref: str, hyp: str) -> None:
        try:
            s = await judge.score(lang, ref, hyp, label=pid)
            out[pid] = (s.score, s.reason, s.cost_usd, s.cached, None)
        except JudgeError as e:
            out[pid] = (None, None, 0.0, False, str(e)[:200])

    try:
        await asyncio.gather(*(one(*it) for it in items))
    finally:
        await judge.aclose()
    return out


def _judge(settings: Settings, run_id: str, model: str, prompt: str, transport, dry_run: bool) -> SemanticJudge:
    guard = None if dry_run else BudgetGuard(settings.spend_path, settings.budget_usd, run_id)
    return SemanticJudge(ResultCache(settings.cache_dir), guard, model=model, prompt=prompt, transport=transport)


def _require_live(transport: httpx.AsyncBaseTransport | None) -> None:
    if transport is None and not credentials_present():
        raise RuntimeError("judge needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (Workers AI) in .env")


SEMANTIC_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "category", "conditions", "judge_model",
    "prompt_version", "semantic_score", "semantic_error", "judge_cost_usd", "cached", "judge_failed",
]  # fmt: skip


@dataclass
class JudgeRunResult:
    plan: Plan
    out_dir: Path | None = None
    n_scored: int = 0
    n_failed: int = 0


def judge_run(
    run_id: str,
    settings: Settings,
    *,
    dry_run: bool = False,
    model: str = DEFAULT_MODEL,
    prompt: str = DEFAULT_PROMPT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> JudgeRunResult:
    """Score every verified (reference, hypothesis) pair of a run. Dry run: estimate only, nothing written."""
    from clipbench.scoring.semantic import semantic_aggregates

    pairs = run_pairs(run_id, settings)
    if pairs is None:
        raise FileNotFoundError(f"run {run_id}: texts or refs are not on this machine (data/runs/{run_id})")
    judge = _judge(settings, run_id, model, prompt, transport, dry_run)
    p = plan([(x.ref, x.hyp) for x in pairs], judge, settings)
    if dry_run:
        return JudgeRunResult(p)
    _require_live(transport)
    check_can_start(p.est_usd, settings.budget_usd, path=settings.spend_path)
    items = list({x.id: (x.id, x.language, x.ref, x.hyp) for x in pairs}.values())
    scores = asyncio.run(_score_all(judge, items))
    records, reasons = [], []
    for x in pairs:
        score, reason, cost, cached, error = scores[x.id]
        records.append(
            {
                "provider": x.provider, "model_id": x.model_id, "video_id": x.video_id, "tier": x.tier,
                "language": x.language, "category": x.category, "conditions": x.conditions,
                "judge_model": model, "prompt_version": judge.prompt.cache_tag, "semantic_score": score,
                "semantic_error": None if score is None else 1 - score, "judge_cost_usd": cost,
                "cached": cached, "judge_failed": error is not None,
            }
        )  # fmt: skip
        reasons.append({"provider": x.provider, "video_id": x.video_id, "reason": reason, "error": error})
    df = pd.DataFrame(records, columns=SEMANTIC_COLUMNS)
    out = settings.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "semantic.parquet", index=False)
    semantic_aggregates(df, settings.sample_seed).to_parquet(out / "semantic_aggregates.parquet", index=False)
    # one line reasons can quote the captions, so they stay in data/
    private = settings.runs_text_dir / run_id
    private.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(reasons).to_parquet(private / "semantic_reasons.parquet", index=False)
    cal = load_calibration(settings)
    if cal is not None:
        (out / "judge_calibration.json").write_text(json.dumps(cal, indent=1))
    return JudgeRunResult(p, out, int(df["semantic_score"].notna().sum()), int(df["judge_failed"].sum()))


# ---------------------------------------------------------------------------
# Labelling (a person scores real pairs)
# ---------------------------------------------------------------------------


def load_labels(settings: Settings) -> pd.DataFrame:
    path = labels_path(settings)
    if not path.exists():
        return pd.DataFrame(columns=LABEL_COLUMNS)
    df = pd.read_csv(path, dtype={"note": "string", "labeller": "string"}, keep_default_na=False)
    df["human_score"] = pd.to_numeric(df["human_score"], errors="coerce")
    return df


def _append_label(settings: Settings, rec: dict[str, Any]) -> None:
    path = labels_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    pd.DataFrame([rec], columns=LABEL_COLUMNS).to_csv(path, mode="a", header=new, index=False)


def candidate_pairs(settings: Settings, language: str) -> list[Pair]:
    """Verified pairs of `language` from every run under data/runs, one per (ref, hyp), non-empty refs."""
    seen: dict[str, Pair] = {}
    for run_dir in sorted(settings.runs_text_dir.glob("*")):
        if not (run_dir / "texts.parquet").exists():
            continue
        pairs = run_pairs(run_dir.name, settings) or []
        for x in pairs:
            if x.language.split("-")[0] == language and x.ref.strip():
                seen.setdefault(x.id, x)
    return list(seen.values())


def parse_label(answer: str) -> tuple[str, float | None, str]:
    """'0.75 missed a name' -> ('score', 0.75, 'missed a name'); 's' skip; 'q' quit."""
    answer = (answer or "").strip()
    if answer.lower() in ("q", "quit"):
        return ("quit", None, "")
    if answer.lower() in ("s", "skip", ""):
        return ("skip", None, "")
    head, _, note = answer.partition(" ")
    try:
        score = float(head)
    except ValueError:
        return ("invalid", None, "")
    if not 0 <= score <= 1:
        return ("invalid", None, "")
    return ("score", score, note.strip())


def label_session(
    settings: Settings,
    language: str,
    labeller: str,
    *,
    n: int = 60,
    seed: int = 7,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> int:
    """Show real (reference, hypothesis) pairs and record the person's score for each. Returns labels saved."""
    if not labeller.strip():
        raise ValueError("a labeller name is required")
    done = load_labels(settings)
    mine = set(done[done["labeller"] == labeller]["pair_id"]) if len(done) else set()
    cands = [x for x in candidate_pairs(settings, language) if x.id not in mine]
    random.Random(seed).shuffle(cands)
    total_lang = int((done["lang"] == language).sum()) if len(done) else 0
    show(
        f"{len(cands)} unlabelled {language} pairs; {total_lang} {language} labels so far (calibration needs "
        f"{MIN_PAIRS}). Score how well the hypothesis keeps the meaning of the reference: 1 same meaning, 0.75 "
        "small loss, 0.5 partial, 0.25 mostly wrong, 0 unrelated. Type a score and an optional note, s to skip, "
        "q to quit. Ignore punctuation, casing, script and filler differences."
    )
    saved = 0
    for x in cands:
        if saved >= n:
            break
        show(f"\n[{saved + 1}/{n}] {x.video_id} ({x.provider})\nREF: {x.ref}\nHYP: {x.hyp}")
        while True:
            kind, score, note = parse_label(ask("score> "))
            if kind != "invalid":
                break
            show("type a number from 0 to 1, s or q")
        if kind == "quit":
            break
        if kind == "skip":
            continue
        _append_label(
            settings,
            {
                "lang": language, "ref": x.ref, "hyp": x.hyp, "human_score": score, "labeller": labeller,
                "note": note, "pair_id": x.id, "run_id": x.run_id, "provider": x.provider,
                "video_id": x.video_id, "labelled_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )  # fmt: skip
        saved += 1
    show(f"saved {saved} labels to {labels_path(settings)}")
    return saved


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def spearman(a: Any, b: Any) -> float:
    """Spearman rank correlation with average ranks for ties. NaN when either side is constant."""
    x = pd.Series(a, dtype=float).rank(method="average").to_numpy()
    y = pd.Series(b, dtype=float).rank(method="average").to_numpy()
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def agreement(human: Any, judge: Any) -> dict[str, Any]:
    h = np.asarray(human, dtype=float)
    j = np.asarray(judge, dtype=float)
    keep = ~(np.isnan(h) | np.isnan(j))
    h, j = h[keep], j[keep]
    n = int(len(h))
    rho = spearman(h, j) if n else float("nan")
    mae = float(np.mean(np.abs(h - j))) if n else float("nan")
    reasons = []
    if n < MIN_PAIRS:
        reasons.append(f"{n} labelled pairs, needs {MIN_PAIRS}")
    if not (rho >= MIN_SPEARMAN):
        reasons.append(f"Spearman {rho:.2f} below {MIN_SPEARMAN}" if not np.isnan(rho) else "Spearman undefined")
    if not (mae <= MAX_MAE):
        reasons.append(f"mean absolute error {mae:.2f} above {MAX_MAE}" if not np.isnan(mae) else "no error measured")
    return {
        "n_pairs": n,
        "spearman": None if np.isnan(rho) else round(rho, 4),
        "mae": None if np.isnan(mae) else round(mae, 4),
        "passes": not reasons,
        "reason": "; ".join(reasons) if reasons else "agreement meets the threshold",
    }


@dataclass
class CalibrationResult:
    plan: Plan
    calibration: dict[str, Any] | None = None


def calibrate(
    settings: Settings,
    *,
    language: str | None = None,
    dry_run: bool = False,
    model: str = DEFAULT_MODEL,
    prompt: str = DEFAULT_PROMPT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CalibrationResult:
    """Judge every human labelled pair and compute agreement per language. Pairs labelled by several people
    use the mean human score. Writes data/calibration/judge_calibration.json (statistics only)."""
    labels = load_labels(settings)
    labels = labels[labels["human_score"].notna()]
    if language:
        labels = labels[labels["lang"] == language]
    by_pair = (
        labels.groupby(["lang", "pair_id"], sort=True)
        .agg(ref=("ref", "first"), hyp=("hyp", "first"), human=("human_score", "mean"), n_labellers=("labeller", "nunique"))
        .reset_index()
    )  # fmt: skip
    judge = _judge(settings, "calibration", model, prompt, transport, dry_run)
    p = plan(list(zip(by_pair["ref"], by_pair["hyp"], strict=True)), judge, settings)
    if dry_run:
        return CalibrationResult(p)
    if len(by_pair):
        _require_live(transport)
        check_can_start(p.est_usd, settings.budget_usd, path=settings.spend_path)
    items = [(r.pair_id, r.lang, r.ref, r.hyp) for r in by_pair.itertuples(index=False)]
    scores = asyncio.run(_score_all(judge, items)) if items else {}
    by_pair["judge"] = [scores[pid][0] if scores[pid][0] is not None else np.nan for pid in by_pair["pair_id"]]
    existing = load_calibration(settings) or {}
    same = existing.get("judge_model") == model and existing.get("prompt_version") == judge.prompt.cache_tag
    langs: dict[str, Any] = dict(existing.get("languages", {})) if same else {}
    for lang, g in by_pair.groupby("lang", sort=True):
        stats = agreement(g["human"], g["judge"])
        stats["n_labellers"] = int(labels[labels["lang"] == lang]["labeller"].nunique())
        stats["judge_failed"] = int(g["judge"].isna().sum())
        langs[str(lang)] = stats
    cal = {
        "judge_model": model,
        "prompt_version": judge.prompt.cache_tag,
        "thresholds": {"min_pairs": MIN_PAIRS, "min_spearman": MIN_SPEARMAN, "max_mae": MAX_MAE},
        "languages": langs,
        "computed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = calibration_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal, indent=1))
    return CalibrationResult(p, cal)


def load_calibration(settings: Settings, run_id: str | None = None) -> dict[str, Any] | None:
    """The local calibration (newest), else the copy published with the run (machines without data/)."""
    paths = [calibration_path(settings)]
    if run_id:
        paths.append(settings.results_dir / run_id / "judge_calibration.json")
    for path in paths:
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except ValueError:
                continue
            if isinstance(data, dict) and not missing(data.get("judge_model")):
                return data
    return None
