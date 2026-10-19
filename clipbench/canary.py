"""Continuous reruns and drift (A3): a frozen 30-clip canary set per language, rerun on a cadence.

Files:

- `datasets/canary/<lang>.csv` (public, committed): the frozen canary clip ids, chosen deterministically
  from the language's verified clips by `clipbench canary select`. Ids only, never text.
- `results/history/<lang>.parquet` (append-only): one row per (canary run, provider, metric) with
  `date, provider, model_id, provider_version, metric, error_rate, ci_low, ci_high, n, run_id`.
- `results/history/drift.parquet`: drift events derived from the history (rebuilt on every append).
- `results/canary/runs/<run_id>/` and `results/canary/<run_id>/`: the canary run's rows and scores,
  kept apart from `results/runs/` so a canary never becomes the `latest` benchmark run.

Money: canaries spend from their own monthly cap (`canary_budget_usd_per_month` in clipbench.toml,
default 2, env CLIPBENCH_CANARY_BUDGET_USD). Spend is the ledger in `data/spend.json` (the same one every
run writes, so the global `budget_usd` cap still applies too); the canary share of a month is the sum of
events whose run id starts with `canary-`, and `canary_by_month` in that file keeps the monthly totals.

A drift event is either a score move (a provider's canary error rate falls outside the bootstrap
interval of its own previous canary run, same metric) or a change of `model_id` or `provider_version`.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import hashlib
import json
import os
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from rich.console import Console

from clipbench.config import Settings, get_settings

CANARY_SIZE = 30
MAX_PER_CHANNEL = 3
DEFAULT_MONTHLY_USD = 2.0
DEFAULT_CADENCE_DAYS = 7
RUN_PREFIX = "canary-"

CANARY_COLUMNS = ["clip_id", "language", "platform", "source_id", "start_ms", "channel", "category", "rank_key"]
HISTORY_COLUMNS = [
    "date", "provider", "model_id", "provider_version", "metric", "error_rate", "ci_low", "ci_high", "n", "run_id",
]  # fmt: skip
DRIFT_COLUMNS = [
    "language", "provider", "metric", "date", "run_id", "kind", "before", "after", "ci_low", "ci_high",
    "model_id_before", "model_id_after", "provider_version_before", "provider_version_after",
]  # fmt: skip


class CanaryError(Exception):
    """The canary command cannot proceed (no set, not enough verified clips, over the monthly cap)."""


class NotEnoughVerified(CanaryError):
    pass


class CanaryBudgetExceeded(CanaryError):
    pass


# ---------------------------------------------------------------- settings


def _toml() -> dict[str, Any]:
    path = get_settings().root / "clipbench.toml"
    try:
        return tomllib.loads(path.read_text()) if path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def monthly_cap() -> float:
    env = os.environ.get("CLIPBENCH_CANARY_BUDGET_USD")
    if env:
        return float(env)
    return float(_toml().get("canary_budget_usd_per_month", DEFAULT_MONTHLY_USD))


def cadence_days() -> int:
    env = os.environ.get("CLIPBENCH_CANARY_CADENCE_DAYS")
    if env:
        return max(1, int(env))
    return max(1, int(_toml().get("canary_cadence_days", DEFAULT_CADENCE_DAYS)))


def canary_dir(settings: Settings | None = None) -> Path:
    env = os.environ.get("CLIPBENCH_CANARY_DIR")
    if env:
        return Path(env)
    base = os.environ.get("CLIPBENCH_DATASETS_DIR")
    return (Path(base) if base else (settings or get_settings()).root / "datasets") / "canary"


def history_dir(settings: Settings) -> Path:
    return settings.results_dir / "history"


@dataclass(frozen=True)
class CanarySettings(Settings):
    """Settings for a canary run: rows, texts and scores live under results/canary and data/canary."""

    base_results: str = ""

    @property
    def results_dir(self) -> Path:
        return Path(self.base_results) / "canary"

    @property
    def runs_text_dir(self) -> Path:
        return self.data_dir / "canary" / "runs"


def canary_settings(settings: Settings, budget_usd: float | None = None) -> CanarySettings:
    fields = {f.name: getattr(settings, f.name) for f in dataclasses.fields(Settings)}
    if budget_usd is not None:
        fields["budget_usd"] = float(budget_usd)
    base = getattr(settings, "base_results", "") or str(settings.results_dir)
    return CanarySettings(**fields, base_results=base)


# ---------------------------------------------------------------- selection


@dataclass(frozen=True)
class Candidate:
    clip_id: str
    language: str
    channel: str | None = None
    category: str | None = None
    platform: str = "youtube"
    source_id: str | None = None
    start_ms: int | None = None


def rank_key(clip_id: str, seed: int) -> str:
    return hashlib.sha256(f"clipbench-canary:{seed}:{clip_id}".encode()).hexdigest()[:16]


def select_canary(
    candidates: list[Candidate], n: int = CANARY_SIZE, seed: int = 7, max_per_channel: int = MAX_PER_CHANNEL
) -> list[Candidate]:
    """Deterministic pick: order verified clips by a seeded hash of the clip id, take them greedily with
    at most `max_per_channel` per channel, then fill from the rest if the channel cap left the set short.
    The same verified set and seed always give the same canary set, whatever order they come in."""
    uniq = {c.clip_id: c for c in candidates}
    if len(uniq) < n:
        raise NotEnoughVerified(
            f"{len(uniq)} verified clips, the canary set needs {n}; verify more clips first"
        )
    ordered = sorted(uniq.values(), key=lambda c: (rank_key(c.clip_id, seed), c.clip_id))
    picked: list[Candidate] = []
    per_channel: Counter[str] = Counter()
    for c in ordered:
        if len(picked) == n:
            break
        ch = c.channel or f"unknown:{c.clip_id}"
        if per_channel[ch] >= max_per_channel:
            continue
        per_channel[ch] += 1
        picked.append(c)
    if len(picked) < n:
        chosen = {c.clip_id for c in picked}
        picked += [c for c in ordered if c.clip_id not in chosen][: n - len(picked)]
    return sorted(picked, key=lambda c: (rank_key(c.clip_id, seed), c.clip_id))


def verified_candidates(lang: str, settings: Settings) -> list[Candidate]:
    """Verified clips of `lang` with audio on disk (what a canary run can actually use)."""
    from clipbench.data.references import load_clips

    out = []
    for c in load_clips("verified", settings=settings, language=lang):
        ref = c.reference
        out.append(
            Candidate(
                clip_id=c.video_id,
                language=c.language,
                channel=c.channel,
                category=ref.category,
                platform=ref.platform,
                source_id=ref.source_id,
                start_ms=ref.start_ms,
            )
        )
    return out


def canary_path(lang: str, settings: Settings | None = None) -> Path:
    return canary_dir(settings) / f"{lang}.csv"


def write_canary(lang: str, picked: list[Candidate], seed: int, settings: Settings | None = None) -> Path:
    path = canary_path(lang, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CANARY_COLUMNS)
        for c in picked:
            w.writerow(
                [c.clip_id, c.language, c.platform, c.source_id or "", "" if c.start_ms is None else c.start_ms,
                 c.channel or "", c.category or "", rank_key(c.clip_id, seed)]
            )  # fmt: skip
    os.replace(tmp, path)
    return path


def load_canary(lang: str, settings: Settings | None = None) -> list[str]:
    """The frozen canary clip ids of `lang`, in file order. Empty when no set has been frozen."""
    path = canary_path(lang, settings)
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [r["clip_id"] for r in csv.DictReader(fh) if r.get("clip_id")]


def canary_sha256(lang: str, settings: Settings | None = None) -> str | None:
    path = canary_path(lang, settings)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def select_and_freeze(
    lang: str, settings: Settings, *, n: int = CANARY_SIZE, reselect: bool = False,
    candidates: list[Candidate] | None = None,
) -> tuple[Path, list[Candidate]]:  # fmt: skip
    path = canary_path(lang, settings)
    if path.exists() and not reselect:
        raise CanaryError(
            f"the {lang} canary set is frozen ({path.name}, {len(load_canary(lang, settings))} clips); "
            "pass --reselect to choose it again (history before and after is then not comparable)"
        )
    cands = verified_candidates(lang, settings) if candidates is None else candidates
    if not cands:
        raise NotEnoughVerified(
            f"no verified {lang} clips with audio yet, so there is no canary set to freeze; it is chosen "
            f"from verified clips once at least {n} exist (clipbench review, then clipbench canary select)"
        )
    picked = select_canary(cands, n=n, seed=settings.sample_seed)
    return write_canary(lang, picked, settings.sample_seed, settings), picked


# ---------------------------------------------------------------- money


def month_of(at: str | None = None) -> str:
    return (at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))[:7]


def canary_spent(spend: dict[str, Any], month: str) -> float:
    total = 0.0
    for e in spend.get("events") or []:
        if str(e.get("run_id", "")).startswith(RUN_PREFIX) and str(e.get("at", "")).startswith(month):
            total += float(e.get("usd") or 0.0)
    return total


def record_month(settings: Settings, month: str) -> float:
    """Write the month's canary total into data/spend.json (`canary_by_month`). Returns it."""
    from clipbench.budget import _atomic_write_json, load_spend

    spend = load_spend(settings.spend_path)
    total = canary_spent(spend, month)
    by_month = dict(spend.get("canary_by_month") or {})
    by_month[month] = round(total, 6)
    spend["canary_by_month"] = by_month
    _atomic_write_json(settings.spend_path, spend)
    return total


def check_month(est_usd: float, spent: float, cap: float, month: str) -> None:
    if est_usd + spent > cap + 1e-12:
        raise CanaryBudgetExceeded(
            f"refusing the canary run: estimated ${est_usd:.4f} + canary spend in {month} ${spent:.4f} = "
            f"${est_usd + spent:.4f}, over the ${cap:.2f} monthly canary cap (canary_budget_usd_per_month in "
            "clipbench.toml or CLIPBENCH_CANARY_BUDGET_USD)"
        )


# ---------------------------------------------------------------- history and drift


def history_path(lang: str, settings: Settings) -> Path:
    return history_dir(settings) / f"{lang}.parquet"


def drift_path(settings: Settings) -> Path:
    return history_dir(settings) / "drift.parquet"


def _atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _typed_history(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=HISTORY_COLUMNS)
    for col in ("date", "provider", "model_id", "provider_version", "metric", "run_id"):
        df[col] = df[col].astype("string")
    for col in ("error_rate", "ci_low", "ci_high"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["n"] = pd.to_numeric(df["n"], errors="coerce").fillna(0).astype("int64")
    return df


def load_history(lang: str, settings: Settings) -> pd.DataFrame:
    path = history_path(lang, settings)
    if not path.exists():
        return _typed_history(pd.DataFrame(columns=HISTORY_COLUMNS))
    return _typed_history(pd.read_parquet(path))


def history_rows(run_id: str, date: str, aggregates: pd.DataFrame, rows: pd.DataFrame | None) -> pd.DataFrame:
    """History rows of one scored canary run: the verified, whole-set aggregate of every provider x metric."""
    agg = aggregates[(aggregates["tier"] == "verified") & (aggregates["slice_kind"] == "all")]
    versions: dict[str, str | None] = {}
    if rows is not None and len(rows):
        ok = rows[(rows["status"] == "ok") & (rows["repeat"] == 0)]
        for pid, grp in ok.groupby("provider"):
            vals = grp["provider_version"].dropna().astype(str)
            versions[str(pid)] = vals.mode().iloc[0] if len(vals) else None
    out = [
        {
            "date": date,
            "provider": str(r.provider),
            "model_id": None if pd.isna(r.model_id) else str(r.model_id),
            "provider_version": versions.get(str(r.provider)),
            "metric": str(r.metric),
            "error_rate": float(r.error_rate),
            "ci_low": float(r.ci_low),
            "ci_high": float(r.ci_high),
            "n": int(r.n_clips),
            "run_id": run_id,
        }
        for r in agg.itertuples()
    ]
    return _typed_history(pd.DataFrame(out, columns=HISTORY_COLUMNS))


def append_history(lang: str, new: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Append-only: existing rows are never changed; a run id that is already in the file is not re-added."""
    old = load_history(lang, settings)
    seen = set(old["run_id"].dropna().astype(str))
    new = new[~new["run_id"].astype(str).isin(seen)]
    if new.empty:
        return old
    both = _typed_history(pd.concat([old, new], ignore_index=True)) if len(old) else _typed_history(new)
    _atomic_parquet(both, history_path(lang, settings))
    return both


def _s(v: Any) -> str | None:
    return None if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)) else str(v)


def drift_events(lang: str, history: pd.DataFrame) -> pd.DataFrame:
    """Drift events in a language's history, each compared with the same provider's previous canary run."""
    events: list[dict[str, Any]] = []
    if history.empty:
        return pd.DataFrame(events, columns=DRIFT_COLUMNS)
    hist = history.sort_values(["date", "run_id"], kind="stable")
    for (provider, metric), grp in hist.groupby(["provider", "metric"], sort=True):
        prev = None
        for cur in grp.itertuples():
            if prev is not None:
                base = {
                    "language": lang, "provider": str(provider), "metric": str(metric), "date": str(cur.date),
                    "run_id": str(cur.run_id), "before": float(prev.error_rate), "after": float(cur.error_rate),
                    "ci_low": float(prev.ci_low), "ci_high": float(prev.ci_high),
                    "model_id_before": _s(prev.model_id), "model_id_after": _s(cur.model_id),
                    "provider_version_before": _s(prev.provider_version),
                    "provider_version_after": _s(cur.provider_version),
                }  # fmt: skip
                if not (prev.ci_low - 1e-12 <= cur.error_rate <= prev.ci_high + 1e-12):
                    events.append({**base, "kind": "score"})
                if _s(prev.model_id) != _s(cur.model_id):
                    events.append({**base, "kind": "model_id"})
                if _s(prev.provider_version) != _s(cur.provider_version):
                    events.append({**base, "kind": "provider_version"})
            prev = cur
    df = pd.DataFrame(events, columns=DRIFT_COLUMNS)
    for col in ("before", "after", "ci_low", "ci_high"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in set(DRIFT_COLUMNS) - {"before", "after", "ci_low", "ci_high"}:
        df[col] = df[col].astype("string")
    return df


def rebuild_drift(settings: Settings) -> pd.DataFrame:
    """drift.parquet from every language's history file."""
    frames = []
    for path in sorted(history_dir(settings).glob("*.parquet")):
        if path.stem in ("drift", "reliability"):
            continue
        frames.append(drift_events(path.stem, _typed_history(pd.read_parquet(path))))
    frames = [f for f in frames if len(f)]
    df = pd.concat(frames, ignore_index=True) if frames else drift_events("", pd.DataFrame())
    _atomic_parquet(df, drift_path(settings))
    return df


# ---------------------------------------------------------------- due


def last_run_date(lang: str, settings: Settings) -> str | None:
    hist = load_history(lang, settings)
    return None if hist.empty else str(hist["date"].max())


def is_due(lang: str, settings: Settings, today: datetime | None = None, days: int | None = None) -> bool:
    last = last_run_date(lang, settings)
    if last is None:
        return True
    today = today or datetime.now(UTC)
    days = cadence_days() if days is None else days
    return datetime.fromisoformat(last[:10]).replace(tzinfo=UTC) + timedelta(days=days) <= today


# ---------------------------------------------------------------- run


@dataclass
class CanaryRun:
    run_id: str
    dry_run: bool
    estimate_usd: float
    month: str
    month_spent: float
    cap: float
    n_clips: int
    missing: list[str]
    history: pd.DataFrame | None = None
    events: pd.DataFrame | None = None


def _canary_clips(lang: str, ids: list[str], settings: Settings) -> tuple[list, list[str]]:
    from clipbench.data.references import load_clips

    by_id = {c.video_id: c for c in load_clips("verified", settings=settings, language=lang)}
    clips = [by_id[i] for i in ids if i in by_id]
    return clips, [i for i in ids if i not in by_id]


async def run_canary(
    lang: str,
    providers: list[Any],
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    clips: list | None = None,
    console: Console | None = None,
    now: datetime | None = None,
) -> CanaryRun:
    """Run `providers` on the frozen canary set of `lang`, append the history and rebuild drift.

    No cache (a canary must ask the provider again), no stability reruns (the bootstrap is over clips).
    """
    from clipbench.budget import estimate, load_spend
    from clipbench.runner import _resolve_providers, run
    from clipbench.scoring.score import score_run

    settings = settings or get_settings()
    console = console or Console(stderr=True)
    ids = load_canary(lang, settings)
    if not ids:
        raise CanaryError(
            f"no canary set for {lang} yet (datasets/canary/{lang}.csv); it is frozen from verified clips "
            f"with clipbench canary select --lang {lang} once {CANARY_SIZE} exist"
        )
    missing: list[str] = []
    if clips is None:
        clips, missing = _canary_clips(lang, ids, settings)
    else:
        wanted = set(ids)
        missing = sorted(wanted - {c.video_id for c in clips})
        clips = [c for c in clips if c.video_id in wanted]
    if missing:
        console.print(f"[yellow]{len(missing)} canary clips have no audio or verified reference here[/yellow]")
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    month = month_of(stamp)
    run_id = f"{RUN_PREFIX}{lang}-{now.strftime('%Y%m%d-%H%M')}"

    active, _skipped = _resolve_providers(list(providers))
    est = estimate(active, clips, stability_n=0, stability_repeats=0)
    spend = load_spend(settings.spend_path)
    spent_month = canary_spent(spend, month)
    cap = monthly_cap()
    result = CanaryRun(run_id, dry_run, est.total_usd, month, spent_month, cap, len(clips), missing)
    if dry_run:
        from clipbench.runner import print_plan

        print_plan(console, est, active, _skipped, spent_month, cap,
                   title=f"canary {lang} plan (dry run, no network; monthly canary cap)")  # fmt: skip
        return result
    if not clips:
        raise CanaryError(f"none of the {lang} canary clips has audio and a verified reference on this machine")
    check_month(est.total_usd, spent_month, cap, month)

    # The runner's guard then stops at whichever is lower: the global cap or what is left this month.
    total_spent = float(spend["total_usd"])
    cs = canary_settings(settings, budget_usd=min(settings.budget_usd, total_spent + (cap - spent_month)))
    await run(active, "verified", clips=clips, run_id=run_id, no_cache=True, settings=cs, console=console,
              stability=False, progress=False, language=lang)  # fmt: skip
    record_month(settings, month)
    out = score_run(run_id, cs)
    aggregates = pd.read_parquet(out / "aggregates.parquet")
    rows = pd.read_parquet(cs.runs_dir / run_id / "rows.parquet")
    from clipbench.scoring.reliability import write_history, write_run_reliability

    write_run_reliability(run_id, settings, runs_dir=cs.runs_dir, out_dir=cs.results_dir / run_id)
    write_history(settings)
    result.history = append_history(lang, history_rows(run_id, stamp[:10], aggregates, rows), settings)
    events = rebuild_drift(settings)
    result.events = events[events["run_id"] == run_id] if len(events) else events
    return result


# ---------------------------------------------------------------- export


def _run_language(run_id: str, settings: Settings) -> str | None:
    path = settings.runs_dir / run_id / "run_meta.json"
    try:
        lang = json.loads(path.read_text()).get("language")
    except (OSError, ValueError):
        return None
    return str(lang).split("-")[0].lower() if lang else None


def _clean(v: Any) -> Any:
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    """`history` (per provider canary series for the run's language) and `drift` (its events)."""
    lang = _run_language(run_id, settings)
    if not lang:
        return None
    hist = load_history(lang, settings)
    out: dict[str, Any] = {"history": None, "drift": None}
    if len(hist):
        series: dict[str, list[dict[str, Any]]] = {}
        for r in hist.sort_values(["provider", "date", "run_id", "metric"]).to_dict("records"):
            point = {k: _clean(r[k]) for k in HISTORY_COLUMNS if k != "provider"}
            series.setdefault(str(r["provider"]), []).append(point)
        out["history"] = {
            "language": lang,
            "canary_clips": len(load_canary(lang, settings)),
            "canary_sha256": canary_sha256(lang, settings),
            "cadence_days": cadence_days(),
            "series": series,
        }
    path = drift_path(settings)
    if path.exists():
        ev = pd.read_parquet(path)
        ev = ev[ev["language"] == lang]
        out["drift"] = [
            {
                **{k: _clean(r[k]) for k in DRIFT_COLUMNS if k not in ("ci_low", "ci_high")},
                "ci": [_clean(r["ci_low"]), _clean(r["ci_high"])],
            }
            for r in ev.sort_values(["date", "provider", "metric", "kind"]).to_dict("records")
        ]
    return out if (out["history"] is not None or out["drift"] is not None) else None


# ---------------------------------------------------------------- CLI


def _langs(lang: str) -> list[str]:
    from clipbench.report.rules import LANGUAGES

    return list(LANGUAGES) if lang == "all" else [lang]


def register(app: typer.Typer) -> None:
    canary_app = typer.Typer(help="Canary reruns and drift (fixed 30-clip set per language).", no_args_is_help=True)

    @canary_app.command("select")
    def select_cmd(
        lang: str = typer.Option(..., "--lang", help="Language, or 'all'."),
        n: int = typer.Option(CANARY_SIZE, "--n", help="Canary set size."),
        reselect: bool = typer.Option(False, "--reselect", help="Replace an already frozen set."),
    ) -> None:
        """Choose and freeze datasets/canary/<lang>.csv from the verified clips (deterministic)."""
        settings = get_settings()
        failed = False
        for one in _langs(lang):
            try:
                path, picked = select_and_freeze(one, settings, n=n, reselect=reselect)
            except CanaryError as exc:
                typer.echo(f"{one}: {exc}", err=True)
                failed = True
                continue
            channels = len({c.channel for c in picked})
            typer.echo(f"{one}: froze {len(picked)} clips from {channels} channels -> {path}")
        if failed and lang != "all":
            raise typer.Exit(1)

    @canary_app.command("run")
    def run_cmd(
        lang: str = typer.Option(..., "--lang", help="Language."),
        providers: str = typer.Option("all", "--providers", help="'all' or a comma separated list of ids."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Price it against the monthly cap. No network."),
    ) -> None:
        """Rerun the canary set, append results/history/<lang>.parquet and update drift.parquet."""
        from clipbench.budget import BudgetExceeded
        from clipbench.providers.registry import resolve

        console = Console(stderr=True)
        try:
            ids = resolve(providers)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        try:
            res = asyncio.run(run_canary(lang, ids, dry_run=dry_run, console=console))
        except (CanaryError, BudgetExceeded) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2 if isinstance(exc, (CanaryBudgetExceeded, BudgetExceeded)) else 1) from exc
        verdict = "within" if res.estimate_usd + res.month_spent <= res.cap + 1e-12 else "OVER"
        typer.echo(
            f"canary {lang}: {res.n_clips} clips, estimate ${res.estimate_usd:.4f}, spent in {res.month} "
            f"${res.month_spent:.4f} of ${res.cap:.2f} ({verdict} the monthly cap)"
        )
        if dry_run:
            if verdict == "OVER":
                raise typer.Exit(2)
            return
        n_events = 0 if res.events is None else len(res.events)
        typer.echo(f"run {res.run_id}: history appended, {n_events} drift events")

    @canary_app.command("due")
    def due_cmd(
        lang: str = typer.Option("all", "--lang", help="Language, or 'all'."),
        force: bool = typer.Option(False, "--force", help="Treat every language with a set as due."),
    ) -> None:
        """Print the languages whose canary is due (frozen set and last run older than the cadence)."""
        settings = get_settings()
        due = []
        for one in _langs(lang):
            if not load_canary(one, settings):
                typer.echo(f"{one}: no canary set yet", err=True)
                continue
            if force or is_due(one, settings):
                due.append(one)
            else:
                typer.echo(f"{one}: not due (last {last_run_date(one, settings)}, every {cadence_days()} days)",
                           err=True)  # fmt: skip
        typer.echo(" ".join(due))

    @canary_app.command("drift")
    def drift_cmd() -> None:
        """Rebuild results/history/drift.parquet from the history files and print the events."""
        df = rebuild_drift(get_settings())
        if df.empty:
            typer.echo("no drift events")
            return
        for r in df.itertuples():
            typer.echo(f"{r.date} {r.language} {r.provider} {r.metric} {r.kind}: {r.before:.4f} -> {r.after:.4f}")

    app.add_typer(canary_app, name="canary")
