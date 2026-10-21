"""Run providers over clips: cache, budget guard, stability reruns, resumable output.

Outputs (see docs/INTERFACES.md):

- results/runs/<run_id>/rows.parquet      numeric rows, one per (provider, clip, repeat)
- results/runs/<run_id>/run_meta.json     commit, versions, model ids, seed
- data/runs/<run_id>/texts.parquet        hypothesis text (never published)
- data/runs/<run_id>/refs.parquet         reference snapshot (never published)

While a run is in progress each finished row is appended to `rows.part.jsonl` (results side, numbers only)
and `texts.part.jsonl` (data side, text). Rerunning with the same run id skips rows that are done and
compacts everything into the parquet files at the end. A row counts as done when it is `ok`, or
`skipped` for a reason other than the budget; errors and budget skips are retried.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import platform
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from clipbench.budget import (
    BUDGET_ERROR,
    BudgetGuard,
    Estimate,
    check_can_start,
    estimate,
    is_billable,
    load_spend,
    repeat_settings_hash,
    stability_subset,
    supports_language,
)
from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Result
from clipbench.providers.base import Provider, ProviderQuotaExhausted, ProviderSkip, load_pricing
from clipbench.providers.cache import ResultCache

ROW_COLUMNS = [
    "run_id",
    "provider",
    "model_id",
    "provider_version",
    "video_id",
    "language",
    "tier",
    "repeat",
    "status",
    "error",
    "latency_ms",
    "audio_seconds",
    "cost_usd",
    "cached",
    "duration_s",
    "channel",
    "tags",
    "hyp_chars",
    # v3 reliability and run options (docs/V3.md)
    "retries",
    "timed_out",
    "http_status",
    "detected_language",
    "options",
    "unsupported_options",  # run options this provider ignored (from raw_response["clipbench_unsupported"])
]
TEXT_COLUMNS = ["provider", "video_id", "repeat", "hyp_text", "words_json", "speakers_json"]
REF_COLUMNS = [
    "video_id", "tier", "ref_text", "words_json", "tags",
    # v2 (docs/V2.md)
    "category", "nonspeech_spans_json", "codeswitch_spans_json", "punctuated", "timing_source",
    "clip_source", "source_id", "start_ms",
    # v3 (docs/V3.md)
    "conditions_json", "speaker_turns_json", "entity_spans_json", "alternatives_json", "itn_spans_json",
]

KEY_PACKAGES = [
    "pydantic",
    "pandas",
    "pyarrow",
    "numpy",
    "jiwer",
    "httpx",
    "rich",
    "typer",
    "soundfile",
    "fugashi",
    "unidic-lite",
    "pykakasi",
    "mlx-whisper",
    "mlx",
    "faster-whisper",
    "ctranslate2",
    "boto3",
]

Key = tuple[str, str, int]


class MixedLanguages(ValueError):
    """The clips of one run are in more than one language."""


class NoClips(ValueError):
    """Nothing to run: no clip has audio and a reference in the requested tier and language."""


class UnverifiedProviders(ValueError):
    """Every provider left for a real run was refused by the verification gate (providers/verification.py)."""


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)


@dataclass
class RunResult:
    run_id: str
    dry_run: bool
    estimate: Estimate
    spent_before: float
    cap: float
    allowed: bool
    skipped_providers: dict[str, str] = field(default_factory=dict)
    rows_path: Path | None = None
    texts_path: Path | None = None
    refs_path: Path | None = None
    meta_path: Path | None = None
    rows: pd.DataFrame | None = None
    n_calls: dict[str, int] = field(default_factory=dict)
    # Verification gate: providers a real run refuses (or, in a dry run, would refuse), and the ones
    # --allow-unverified let through. {provider id: reason}
    refused_providers: dict[str, str] = field(default_factory=dict)
    allowed_unverified: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def _resolve_providers(specs: list[str | Provider]) -> tuple[list[Provider], dict[str, str]]:
    from clipbench.providers import registry

    ok: list[Provider] = []
    skipped: dict[str, str] = {}
    for spec in specs:
        if isinstance(spec, Provider):
            p = spec
        else:
            if spec not in registry.REGISTRY:
                raise KeyError(f"unknown provider {spec!r}; known: {', '.join(registry.REGISTRY)}")
            try:
                p = registry.make(spec)
            except Exception as exc:  # adapter module missing, bad config
                skipped[spec] = f"{spec}: skipped, could not load adapter ({type(exc).__name__}: {exc})"
                continue
        try:
            available, message = p.availability()
        except Exception as exc:
            available, message = False, f"{p.id}: skipped, availability check failed ({exc})"
        if available and p.paid and p.price_per_minute() <= 0:
            # Estimated and reserved at $0, a paid request could never be stopped by the budget guard.
            available = False
            message = f"{p.id}: skipped, paid provider with no price for {p.model_id} in pricing.toml"
        if available:
            ok.append(p)
        else:
            skipped[p.id] = message
    return ok, skipped


def _git_info(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10
        )
        if commit.returncode != 0:
            return None, None
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return commit.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def _package_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in KEY_PACKAGES:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return out


def _atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _atomic_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn last line from a crash
    return out


def _scrub_paths(message: str | None, settings: Settings) -> str | None:
    """Messages go into published files (rows.parquet, run_meta.json): name local paths relative to the
    data dir, the repo or the home directory, never absolutely."""
    if not message:
        return message
    for base, label in ((settings.data_dir, "data"), (settings.root, "."), (Path.home(), "~")):
        for form in {str(base), str(Path(base).resolve())}:
            if form and form != "/":
                message = message.replace(form, label)
    return message


def _end_with_newline(path: Path) -> None:
    """Terminate a torn last line so the next append starts a new record instead of joining it."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb") as fh:
        fh.seek(-1, os.SEEK_END)
        last = fh.read(1)
    if last != b"\n":
        with path.open("a") as fh:
            fh.write("\n")


def _key(row: dict[str, Any]) -> Key:
    return (str(row["provider"]), str(row["video_id"]), int(row["repeat"]))


QUOTA_ERROR = "quota"  # provider account out of quota or credit; retried on resume, like budget skips


def _is_done(row: dict[str, Any]) -> bool:
    if row.get("status") == "ok":
        return True
    return row.get("status") == "skipped" and row.get("error") not in (BUDGET_ERROR, QUOTA_ERROR)


def _unsupported(result: Result) -> str | None:
    raw = result.raw_response
    names = raw.get("clipbench_unsupported") if isinstance(raw, dict) else None
    return json.dumps(sorted(names)) if names else None


def _words_json(result: Result | None) -> str | None:
    if result is None or result.words is None:
        return None
    return json.dumps([w.model_dump() for w in result.words], ensure_ascii=False)


def _rows_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=ROW_COLUMNS)
    df["repeat"] = df["repeat"].astype("int64")
    df["cached"] = df["cached"].astype(bool)
    for col in ("latency_ms", "audio_seconds", "cost_usd", "duration_s"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["hyp_chars"] = pd.to_numeric(df["hyp_chars"], errors="coerce").astype("Int64")
    df["retries"] = pd.to_numeric(df["retries"], errors="coerce").fillna(0).astype("int64")
    df["timed_out"] = df["timed_out"].fillna(False).astype(bool)
    df["http_status"] = pd.to_numeric(df["http_status"], errors="coerce").astype("Int64")
    df["detected_language"] = df["detected_language"].astype("string")
    df["options"] = df["options"].astype("string")
    df["unsupported_options"] = df["unsupported_options"].astype("string")
    for col in ("run_id", "provider", "model_id", "provider_version", "video_id", "language", "tier",
                "status", "error", "channel", "tags"):
        df[col] = df[col].astype("string")
    return df.sort_values(["provider", "video_id", "repeat"]).reset_index(drop=True)


def _texts_frame(texts: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(texts, columns=TEXT_COLUMNS)
    df["repeat"] = df["repeat"].astype("int64")
    for col in ("provider", "video_id", "hyp_text", "words_json", "speakers_json"):
        df[col] = df[col].astype("string")
    return df.sort_values(["provider", "video_id", "repeat"]).reset_index(drop=True)


def _refs_frame(clips: list[Clip]) -> pd.DataFrame:
    recs = []
    for c in clips:
        ref = c.reference
        recs.append(
            {
                "video_id": c.video_id,
                "tier": ref.tier,
                "ref_text": ref.text,
                "words_json": None
                if ref.words is None
                else json.dumps([w.model_dump() for w in ref.words], ensure_ascii=False),
                "tags": json.dumps(list(ref.tags), ensure_ascii=False),
                "category": ref.category,
                "nonspeech_spans_json": json.dumps([s.model_dump() for s in ref.nonspeech_spans]),
                "codeswitch_spans_json": json.dumps([s.model_dump() for s in ref.codeswitch_spans]),
                "punctuated": "true" if ref.punctuated else "false",
                "timing_source": ref.timing_source,
                "clip_source": ref.clip_source,
                "source_id": ref.source_id,
                "start_ms": None if ref.start_ms is None else str(ref.start_ms),
                "conditions_json": json.dumps(list(ref.conditions)),
                "speaker_turns_json": json.dumps([t.model_dump() for t in ref.speaker_turns]),
                "entity_spans_json": json.dumps([e.model_dump() for e in ref.entity_spans]),
                "alternatives_json": json.dumps([a.model_dump() for a in ref.alternatives], ensure_ascii=False),
                "itn_spans_json": json.dumps([i.model_dump() for i in ref.itn_spans]),
            }
        )
    df = pd.DataFrame(recs, columns=REF_COLUMNS)
    for col in REF_COLUMNS:
        df[col] = df[col].astype("string")
    return df.sort_values("video_id").reset_index(drop=True)


def _provider_meta(p: Provider) -> dict[str, Any]:
    from clipbench.providers.verification import verification

    pricing = load_pricing().get(p.id, {})
    v = verification(p.id)
    return {
        "id": p.id,
        "model_id": p.model_id,
        "settings": p.settings(),
        "settings_hash": p.settings_hash(),
        "paid": bool(p.paid),
        "uploads_audio": bool(p.uploads_audio),
        "usd_per_minute": p.price_per_minute(),
        "pricing_checked_on": pricing.get("checked_on"),
        "pricing_checked_url": pricing.get("checked_url"),
        "live_verified": v.live_verified,
        "price_status": v.price_status,
        "policy_status": v.policy_status,
    }


def _verification_notes(pid: str, refused: dict[str, str] | None, overridden: dict[str, str] | None) -> list[str]:
    if refused and pid in refused:
        return [f"would be refused: {refused[pid]}"]
    if overridden and pid in overridden:
        return [f"unverified, allowed by --allow-unverified ({overridden[pid]})"]
    return []


def _print_refusal_summary(console: Console, refused: dict[str, str] | None) -> None:
    if refused:
        console.print(
            f"[red]a real run would refuse {', '.join(sorted(refused))}: price or data policy not documented "
            "(see docs/providers.md, Verification status); name them in --allow-unverified to run anyway[/red]"
        )


# ---------------------------------------------------------------- dry run table


def print_plan(
    console: Console,
    est: Estimate,
    providers: list[Provider],
    skipped: dict[str, str],
    spent: float,
    cap: float,
    *,
    title: str = "clipbench run plan (dry run, no network)",
    refused: dict[str, str] | None = None,
    overridden: dict[str, str] | None = None,
    stopped_before: dict[str, tuple[str, str]] | None = None,
) -> bool:
    """`stopped_before`: provider -> (run id, "quota" | "budget") of the latest run of this language and tier
    that stopped it, so the plan can say where to resume."""
    table = Table(title=title)
    for col in ("provider", "available", "model", "clips", "audio min", "est cost (incl stability)", "note"):
        table.add_column(col, justify="right" if col in ("clips", "audio min") else "left")
    for p in providers:
        e = est.providers[p.id]
        note = []
        if e.stability_repeats:
            note.append(f"stability {e.stability_clips} x {e.stability_repeats}")
        if e.cached_requests:
            note.append(f"{e.cached_requests} cached")
        if not is_billable(p):
            note.append("free")
        note += _verification_notes(p.id, refused, overridden)
        if stopped_before and p.id in stopped_before:
            prev, why = stopped_before[p.id]
            note.append(f"stopped by {'account quota' if why == QUOTA_ERROR else 'the budget cap'} in {prev}")
        table.add_row(
            p.id, "yes", p.model_id, str(e.n_clips), f"{e.audio_seconds / 60:.1f}", f"${e.total_usd:.4f}",
            ", ".join(note),
        )
    for pid, msg in skipped.items():
        table.add_row(pid, "no", "-", "-", "-", "-", msg)
    console.print(table)
    # A real run drops refused providers, so they are not part of what it would spend.
    refused_ids = set(refused or {})
    refused_usd = sum(e.total_usd for pid, e in est.providers.items() if pid in refused_ids)
    total = est.total_usd - refused_usd
    allowed = total + spent <= cap + 1e-12
    n_prov = len(est.providers)
    per = max((e.audio_seconds for e in est.providers.values()), default=0.0)
    console.print(
        f"audio minutes: {per / 60:.1f} per provider ({est.total_audio_seconds / 60:.1f} over {n_prov} "
        f"provider{'s' if n_prov != 1 else ''})"
    )
    console.print(f"total estimated cost: ${total:.4f}")
    if refused_usd > 0:
        console.print(f"  plus ${refused_usd:.4f} for refused providers, only if named in --allow-unverified")
    console.print(f"spent so far: ${spent:.4f}")
    console.print(f"cap: ${cap:.2f}")
    console.print(
        "a real run would be allowed" if allowed else "a real run would be REFUSED (estimate + spent > cap)"
    )
    _print_refusal_summary(console, refused)
    if stopped_before:
        runs = sorted({r for r, _ in stopped_before.values()})
        console.print(
            f"stopped before: {', '.join(sorted(stopped_before))}. To fill in their missing clips once the "
            f"account is topped up (or the cap raised), resume with --run-id {runs[-1]}"
            + (f" (also stopped in: {', '.join(runs[:-1])})" if len(runs) > 1 else "")
        )
    return allowed


def stopped_history(settings: Settings, language: str | None, tier: str) -> dict[str, tuple[str, str]]:
    """provider -> (run id, reason) for providers whose latest run in this language and tier was stopped
    by account quota or the budget cap. A later run of the provider that was not stopped clears it."""
    metas: list[tuple[str, str, dict]] = []
    if not settings.runs_dir.exists():
        return {}
    for path in settings.runs_dir.glob("*/run_meta.json"):
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("tier") != tier or (language and str(meta.get("language", "")).split("-")[0] != language):
            continue
        metas.append((str(meta.get("updated_at") or meta.get("created_at") or ""), path.parent.name, meta))
    out: dict[str, tuple[str, str]] = {}
    for _, run_id, meta in sorted(metas):
        quota = set(meta.get("quota_stopped") or [])
        budget = set(meta.get("budget_stopped") or [])
        for p in meta.get("providers") or []:
            pid = p.get("id") if isinstance(p, dict) else None
            if not pid:
                continue
            if pid in quota:
                out[pid] = (run_id, QUOTA_ERROR)
            elif pid in budget:
                out[pid] = (run_id, BUDGET_ERROR)
            else:
                out.pop(pid, None)
    return out


def _print_locked_plan(
    console: Console,
    skipped: dict[str, str],
    clips: list[Clip],
    est: Estimate,
    spent: float,
    cap: float,
    stability_n: int,
    stability_repeats: int,
    seed: int,
    allow: frozenset[str] = frozenset(),
) -> None:
    """What the providers that are only missing keys would cost, so the owner sees it before adding them."""
    from clipbench.providers import registry
    from clipbench.providers.verification import check

    locked: list[Provider] = []
    for pid in skipped:
        if pid not in registry.REGISTRY:
            continue
        try:
            p = registry.make(pid)
        except Exception:
            continue
        if is_billable(p) and supports_language(p, clips[0].language if clips else None):
            locked.append(p)
    if not locked:
        return
    extra = estimate(locked, clips, stability_n=stability_n, stability_repeats=stability_repeats, seed=seed)
    table = Table(title="if you add the missing keys (estimate from pricing.toml)")
    refused, overridden = check(locked, allow)
    for col in ("provider", "model", "usd/min", "clips", "est cost (incl stability)", "note"):
        table.add_column(col, justify="left" if col in ("provider", "model", "note") else "right")
    for p in locked:
        e = extra.providers[p.id]
        note = ", ".join(_verification_notes(p.id, refused, overridden))
        table.add_row(
            p.id, p.model_id, f"{p.price_per_minute():.5f}", str(e.n_clips), f"${e.total_usd:.4f}", note
        )
    console.print(table)
    _print_refusal_summary(console, refused)
    total = est.total_usd + extra.total_usd
    verdict = "allowed" if total + spent <= cap + 1e-12 else "REFUSED (estimate + spent > cap)"
    console.print(f"total with every provider: ${total:.4f} (spent ${spent:.4f}, cap ${cap:.2f}): {verdict}")


# ---------------------------------------------------------------- run


async def run(
    providers: list[str | Provider],
    tier: str = "verified",
    clips: list[Clip] | None = None,
    run_id: str | None = None,
    no_cache: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    stability_n: int = 20,
    stability_repeats: int = 3,
    seed: int = 7,
    *,
    settings: Settings | None = None,
    console: Console | None = None,
    stability: bool = True,
    progress: bool = True,
    language: str | None = None,
    options: dict[str, Any] | None = None,
    allow_unverified: str | list[str] | frozenset[str] | None = None,
    sequential_providers: bool = True,
    max_concurrency: int | None = 4,
) -> RunResult:
    """Run `providers` (ids or Provider instances) over `clips` (default: load_clips(tier)).

    A run holds exactly one language: scores, reports and exports are per language, and metrics with
    the same name (en, es and hi all use `wer`) would otherwise be pooled. `language` keeps only that
    language's clips; without it, clips in more than one language raise ValueError.

    A real run refuses a paid provider whose price or data policy is not documented in pricing.toml
    (clipbench/providers/verification.py) unless its id is in `allow_unverified`; the override is recorded in
    run_meta. A dry run prices refused providers and marks them "would be refused".
    """
    from clipbench.providers.verification import check, parse_allow, refused_message

    settings = settings or get_settings()
    console = console or Console(stderr=True)
    if not stability:
        stability_n = 0
        stability_repeats = 0

    if clips is None:
        from clipbench.data.references import load_clips  # lazy: written by another module

        clips = load_clips(tier, settings=settings)
    if language:
        clips = [c for c in clips if c.language.split("-")[0].lower() == language.split("-")[0].lower()]
    langs = sorted({c.language for c in clips})
    if len(langs) > 1:
        raise MixedLanguages(
            f"a run holds one language, but the clips are in {', '.join(langs)}; pass --lang "
            "(or language=) to pick one"
        )
    clips = sorted(clips, key=lambda c: c.video_id)
    if limit is not None:
        clips = clips[:limit]

    active, skipped = _resolve_providers(providers)
    run_lang = langs[0] if langs else language
    for p in [p for p in active if not supports_language(p, run_lang)]:
        # Pricing (and calling) an English-only model on a Japanese run would only produce skips.
        active.remove(p)
        skipped[p.id] = f"{p.id}: skipped, {p.model_id} does not support language {run_lang}"
    allow = parse_allow(allow_unverified)
    refused, overridden = check(active, allow)
    if not dry_run and refused:
        active = [p for p in active if p.id not in refused]
        for pid, reason in refused.items():
            skipped[pid] = refused_message(pid, reason)
            console.print(f"[red]{skipped[pid]}[/red]")
        if not active:
            raise UnverifiedProviders(
                "refusing to start: every provider was refused by the verification gate "
                f"({'; '.join(refused_message(pid, r) for pid, r in refused.items())})"
            )
    for pid, reason in overridden.items():
        console.print(f"[yellow]{pid}: unverified ({reason}), allowed by --allow-unverified[/yellow]")
    run_options = {k: v for k, v in (options or {}).items() if v not in (None, False, "", [])}
    if run_options:
        # Run options (diarize, detect_language, keyterms) are provider options, so they are part of the
        # cache key; adapters that do not support one record it in raw_response["clipbench_unsupported"].
        for p in active:
            p.options.update(run_options)
    for pid, msg in skipped.items():
        if pid not in refused:
            console.print(f"[yellow]{msg}[/yellow]")

    cache = ResultCache(settings.cache_dir)
    est = estimate(
        active,
        clips,
        stability_n=stability_n,
        stability_repeats=stability_repeats,
        seed=seed,
        cache=None if no_cache else cache,
    )
    spent_before = float(load_spend(settings.spend_path)["total_usd"])
    cap = settings.budget_usd
    run_id = run_id or new_run_id()

    if not clips:
        # A plan over zero clips would print "$0, allowed", which reads like a working setup.
        where = f"tier {tier}" + (f", language {language}" if language else "") + (
            f", --limit {limit}" if limit is not None else ""
        )
        hint = (
            " Nothing is verified yet? Verified clips come from `clipbench review`; to run on creator captions "
            "use --tier creator."
            if tier == "verified"
            else ""
        )
        msg = (
            f"no clips to run ({where}): a clip needs a WAV with its hash in data/audio and a reference in "
            f"that tier.{hint}"
        )
        if not dry_run:
            raise NoClips(msg)
        console.print(f"[red]{msg} The plan below covers zero clips.[/red]")
    if dry_run:
        stopped_prev = {
            pid: v for pid, v in stopped_history(settings, run_lang, tier).items() if pid in {p.id for p in active}
        }
        allowed = print_plan(
            console, est, active, skipped, spent_before, cap, refused=refused, overridden=overridden,
            stopped_before=stopped_prev,
        )  # fmt: skip
        _print_locked_plan(
            console, skipped, clips, est, spent_before, cap, stability_n, stability_repeats, seed, allow
        )
        return RunResult(
            run_id, True, est, spent_before, cap, allowed, skipped,
            refused_providers=refused, allowed_unverified=overridden,
        )  # fmt: skip

    check_can_start(est, cap, spent_before)

    rows_dir = settings.runs_dir / run_id
    text_dir = settings.runs_text_dir / run_id
    rows_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    rows_path = rows_dir / "rows.parquet"
    rows_part = rows_dir / "rows.part.jsonl"
    texts_path = text_dir / "texts.parquet"
    texts_part = text_dir / "texts.part.jsonl"
    refs_path = text_dir / "refs.parquet"
    meta_path = rows_dir / "run_meta.json"

    # Previous state for resume: compacted parquet first, then the part files on top.
    prev_rows: dict[Key, dict[str, Any]] = {}
    prev_texts: dict[Key, dict[str, Any]] = {}
    if rows_path.exists():
        for r in pd.read_parquet(rows_path).astype(object).where(lambda d: d.notna(), None).to_dict("records"):
            prev_rows[_key(r)] = r
    if texts_path.exists():
        for t in pd.read_parquet(texts_path).astype(object).where(lambda d: d.notna(), None).to_dict("records"):
            prev_texts[_key(t)] = t
    for r in _read_jsonl(rows_part):
        prev_rows[_key(r)] = r
    for t in _read_jsonl(texts_part):
        prev_texts[_key(t)] = t
    # An ok row only counts as done with its hypothesis: a crash between the two appends (or a torn
    # text line) must redo the row (usually a free cache hit), never score it as an empty output.
    done = {k for k, r in prev_rows.items() if _is_done(r) and (r.get("status") != "ok" or k in prev_texts)}
    for part in (rows_part, texts_part):
        _end_with_newline(part)

    guard = BudgetGuard(settings.spend_path, cap, run_id)
    write_lock = asyncio.Lock()
    new_rows: dict[Key, dict[str, Any]] = {}
    new_texts: dict[Key, dict[str, Any]] = {}
    stopped: dict[str, bool] = {p.id: False for p in active}
    stop_reason: dict[str, str] = {}
    calls: dict[str, int] = {p.id: 0 for p in active}
    subset = stability_subset(clips, stability_n, seed)

    jobs: list[tuple[Provider, Clip, int]] = []
    for p in active:
        for c in clips:
            jobs.append((p, c, 0))
        if p.paid and stability_repeats > 0:
            for r in range(1, stability_repeats + 1):
                for c in subset:
                    jobs.append((p, c, r))

    def base_row(p: Provider, c: Clip, repeat: int) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "provider": p.id,
            "model_id": p.model_id,
            "provider_version": None,
            "video_id": c.video_id,
            "language": c.language,
            "tier": c.tier,
            "repeat": repeat,
            "status": "ok",
            "error": None,
            "latency_ms": None,
            "audio_seconds": c.duration_s,
            "cost_usd": 0.0,
            "cached": False,
            "duration_s": c.duration_s,
            "channel": c.channel,
            "tags": json.dumps(list(c.tags), ensure_ascii=False),
            "hyp_chars": None,
            "retries": 0,
            "timed_out": False,
            "http_status": None,
            "detected_language": None,
            "options": json.dumps(run_options, sort_keys=True),
            "unsupported_options": None,
        }

    async def emit(row: dict[str, Any], result: Result | None) -> None:
        key = _key(row)
        row["error"] = _scrub_paths(row.get("error"), settings)
        text = None
        if result is not None:
            text = {
                "provider": row["provider"],
                "video_id": row["video_id"],
                "repeat": row["repeat"],
                "hyp_text": result.text,
                "words_json": _words_json(result),
                "speakers_json": None if result.speakers is None else json.dumps(result.speakers),
            }
        async with write_lock:
            # Text first: a row that is on disk always has its hypothesis on disk too.
            if text is not None:
                new_texts[key] = text
                with texts_part.open("a") as fh:
                    fh.write(json.dumps(text, ensure_ascii=False) + "\n")
            new_rows[key] = row
            with rows_part.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def ok_row(row: dict[str, Any], result: Result, cached: bool) -> dict[str, Any]:
        row.update(
            status="ok",
            model_id=result.model_id or row["model_id"],
            provider_version=result.provider_version,
            latency_ms=float(result.latency_ms),
            audio_seconds=float(result.audio_seconds),
            cost_usd=float(result.cost_usd),
            cached=cached,
            hyp_chars=len(result.text),
            # A cache hit made no request: its stored retries and status belong to the original call and
            # would inflate the reliability board.
            retries=0 if cached else int(result.retries or 0),
            http_status=None if cached else result.http_status,
            detected_language=result.detected_language,
            unsupported_options=_unsupported(result),
        )
        return row

    # Latency is only comparable when providers do not compete for the same uplink: by default one provider
    # runs at a time with at most `max_concurrency` requests in flight (measured live: Deepgram took 4.6 s
    # alone and 38 s while nine other providers uploaded at the same time).
    def _slots(p: Provider) -> int:
        n = max(1, int(p.concurrency))
        return min(n, max_concurrency) if max_concurrency else n

    sems = {p.id: asyncio.Semaphore(_slots(p)) for p in active}

    async def do_job(p: Provider, c: Clip, repeat: int, advance) -> None:
        try:
            await _do_job(p, c, repeat)
        finally:
            advance(p.id)

    async def _do_job(p: Provider, c: Clip, repeat: int) -> None:
        key: Key = (p.id, c.video_id, repeat)
        if key in done:
            return
        row = base_row(p, c, repeat)
        shash = repeat_settings_hash(p, repeat)
        if not no_cache:
            # Cache hits are free, so they are served even after the budget stopped this provider.
            hit = cache.get(p.id, p.model_id, c.audio_sha256, shash)
            if hit is not None:
                await emit(ok_row(row, hit, cached=True), hit)
                return
        billable = is_billable(p)
        async with sems[p.id]:
            if stopped[p.id]:
                row.update(status="skipped", error=stop_reason.get(p.id, BUDGET_ERROR))
                await emit(row, None)
                return
            reserved = 0.0
            if billable:
                reserved = p.estimate_cost(c.duration_s)
                if not await guard.reserve(reserved):
                    if not stopped[p.id]:
                        console.print(
                            f"[red]{p.id}: stopping, the next request would cross the ${cap:.2f} cap[/red]"
                        )
                    stopped[p.id] = True
                    row.update(status="skipped", error=BUDGET_ERROR)
                    await emit(row, None)
                    return
            calls[p.id] += 1
            t0 = time.perf_counter()
            job_timeout = float(p.job_timeout_s())
            try:
                result = await asyncio.wait_for(
                    p.transcribe(Path(c.audio_path), c.language, clip=c), timeout=job_timeout
                )
            except ProviderSkip as exc:
                if billable:
                    await guard.release(reserved)
                row.update(status="skipped", error=str(exc) or "skipped")
                await emit(row, None)
                return
            except TimeoutError:
                # The request may have been billed even though we gave up: count the estimate as spent.
                if billable:
                    await guard.record(p.id, c.video_id, reserved, reserved)
                row.update(
                    status="error",
                    timed_out=True,
                    error=f"timeout after {job_timeout:.0f}s",
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                )
                await emit(row, None)
                return
            except ProviderQuotaExhausted as exc:
                # Out of quota or credit: every further clip would fail the same way, so stop this provider
                # and leave its clips for a resume once the account is topped up.
                if billable:
                    await guard.release(reserved)
                if not stopped[p.id]:
                    console.print(f"[red]{p.id}: stopping, the account is out of quota: {str(exc)[:200]}[/red]")
                stopped[p.id] = True
                stop_reason[p.id] = QUOTA_ERROR
                row.update(
                    status="skipped",
                    error=QUOTA_ERROR,
                    http_status=getattr(exc, "http_status", None),
                    retries=int(getattr(exc, "retries", 0) or 0),
                )
                await emit(row, None)
                return
            except Exception as exc:
                if billable:
                    await guard.release(reserved)
                row.update(
                    status="error",
                    http_status=getattr(exc, "http_status", None) or getattr(exc, "status_code", None),
                    retries=int(getattr(exc, "retries", 0) or 0),
                    error=f"{type(exc).__name__}: {exc}"[:500],
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                )
                await emit(row, None)
                return
            if billable:
                await guard.record(p.id, c.video_id, result.cost_usd, reserved)
            cache.put(p.id, p.model_id, c.audio_sha256, shash, result)
            await emit(ok_row(row, result, cached=False), result)

    show = progress and console.is_terminal
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=not show,
        transient=False,
    ) as bar:
        tasks = {
            p.id: bar.add_task(p.id, total=sum(1 for j in jobs if j[0].id == p.id)) for p in active
        }

        def advance(pid: str) -> None:
            bar.advance(tasks[pid])

        if sequential_providers:
            for p in active:
                await asyncio.gather(*(do_job(q, c, r, advance) for q, c, r in jobs if q.id == p.id))
        else:
            await asyncio.gather(*(do_job(p, c, r, advance) for p, c, r in jobs))

    # Compact: previous state, then this pass on top.
    all_rows = {**prev_rows, **new_rows}
    all_texts = {**prev_texts, **new_texts}
    # A row that is no longer ok must not keep an old hypothesis.
    all_texts = {k: v for k, v in all_texts.items() if k in all_rows and all_rows[k].get("status") == "ok"}
    rows_df = _rows_frame(list(all_rows.values()))
    _atomic_parquet(rows_df, rows_path)
    _atomic_parquet(_texts_frame(list(all_texts.values())), texts_path)
    refs_df = _refs_frame(clips)
    if refs_path.exists():
        # A resume over fewer clips (--limit) keeps the snapshot of the clips it did not touch.
        old_refs = pd.read_parquet(refs_path)
        keep = old_refs[~old_refs["video_id"].isin(set(refs_df["video_id"]))]
        if len(keep):
            keep = keep.reindex(columns=REF_COLUMNS).astype("string")
            refs_df = pd.concat([refs_df, keep], ignore_index=True).sort_values("video_id").reset_index(drop=True)
    _atomic_parquet(refs_df, refs_path)
    rows_part.unlink(missing_ok=True)
    texts_part.unlink(missing_ok=True)

    from clipbench import __version__

    commit, dirty = _git_info(settings.root)
    old_meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            old_meta = json.loads(meta_path.read_text())
        except ValueError:
            old_meta = {}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "run_id": run_id,
        "created_at": old_meta.get("created_at", now),
        "updated_at": now,
        "git_commit": commit,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": _package_versions(),
        "clipbench_version": __version__,
        # A resume with fewer providers keeps the meta of the providers that have rows from before.
        "providers": [_provider_meta(p) for p in active]
        + [
            m
            for m in old_meta.get("providers") or []
            if isinstance(m, dict)
            and m.get("id") not in {p.id for p in active}
            and m.get("id") in set(rows_df["provider"].astype(str))
        ],
        "skipped_providers": {pid: _scrub_paths(msg, settings) for pid, msg in skipped.items()},
        # Verification gate (providers/verification.py): who was refused, and who ran unverified because
        # the operator named them in --allow-unverified, with the reason that was overridden.
        "verification_refused": refused,
        "allow_unverified": overridden,
        "budget_stopped": [pid for pid, s in stopped.items() if s and stop_reason.get(pid) != QUOTA_ERROR],
        "quota_stopped": [pid for pid, s in stopped.items() if s and stop_reason.get(pid) == QUOTA_ERROR],
        "sample_seed": settings.sample_seed,
        "stability_seed": seed,
        "stability_n": stability_n,
        "stability_repeats": stability_repeats,
        "kind": "batch",
        "options": run_options,
        "latency_mode": {
            "sequential_providers": sequential_providers,
            "max_concurrency": max_concurrency,
            "note": "one provider at a time" if sequential_providers else "providers in parallel (latency contended)",
        },
        "tier": tier,
        "language": clips[0].language if clips else (language or settings.language),
        "n_clips": int(rows_df["video_id"].nunique()) if len(rows_df) else len(clips),
        "no_cache": no_cache,
        "estimate_usd": est.total_usd,
        "spent_before_usd": spent_before,
        "spent_after_usd": guard.spent,
        "budget_cap_usd": cap,
    }
    _atomic_json(meta, meta_path)

    return RunResult(
        run_id=run_id,
        dry_run=False,
        estimate=est,
        spent_before=spent_before,
        cap=cap,
        allowed=True,
        skipped_providers=skipped,
        rows_path=rows_path,
        texts_path=texts_path,
        refs_path=refs_path,
        meta_path=meta_path,
        rows=rows_df,
        n_calls=calls,
        refused_providers=refused,
        allowed_unverified=overridden,
    )
