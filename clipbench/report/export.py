"""clipbench export <run_id|latest>: run data as JSON for the web app.

Writes web/public/data/runs/<run_id>.json and updates web/public/data/index.json, both exactly as
docs/WEB.md describes. Inputs are the published numbers only:

- results/<run_id>/{aggregates,paired,stability,scores}.parquet
- results/runs/<run_id>/run_meta.json (and rows.parquet for clip channels)
- results/<run_id>/errors.json (substitution pairs and short snippets, never full captions)
- clipbench/providers/pricing.toml for prices the run meta does not carry

Findings come from clipbench.report.findings and limitations from clipbench.report.report, so the
web app and the static report say the same thing. No caption text is exported. From data/ only metadata
is read: the category saved in each verified file (the publication gate's diversity check) and file
presence for the dataset funnel.

Publication gate (clipbench.report.rules, docs/V3.md "Publication gate fields"): every aggregates row
carries `eligible` and `coverage`; `paired` holds only tests between eligible providers, Benjamini-Hochberg
adjusted (verdict, verdict_raw, p_adjusted); `ranking` is the one ranking order, empty when unpublished.
The output directory is web/public/data under the repo root, or $CLIPBENCH_WEB_DIR/public/data.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
import typer

from clipbench.config import Settings, get_settings
from clipbench.report import findings as findings_mod
from clipbench.report import rules
from clipbench.report.report import RunData, _pricing_toml, latency_mode, limitations, load_run

TIERS = ("verified", "creator")

METRIC_LABELS = {
    "cer": "Character error rate",
    "cer_reading": "Reading-normalised CER",
    "wer": "Word error rate",
    "wer_no_accents": "WER ignoring accents",
    "cer_nospace": "CER ignoring spaces",
    "wer_syllable": "Syllable WER",
    "wer_translit": "WER after transliteration",
}

# Window clip ids look like "<video_id>@<start_ms>-<end_ms>" (docs/V2.md).
_WINDOW_RE = re.compile(r"^(?P<vid>.+)@(?P<start>\d+)-(?P<end>\d+)$")

PROVIDER_LABELS = {
    "whisper-local": "Whisper large-v3 (local)",
    "whisper-local-turbo": "Whisper large-v3 turbo (local)",
    "groq-whisper": "Groq Whisper",
    "deepgram": "Deepgram",
    "assemblyai": "AssemblyAI",
    "elevenlabs-scribe": "ElevenLabs Scribe",
    "gemini-flash": "Gemini Flash",
    "aws-transcribe": "AWS Transcribe",
    "cf-whisper-large-v3-turbo": "Whisper large-v3 turbo (Workers AI)",
    "cf-whisper": "Whisper (Workers AI)",
    "cf-whisper-tiny-en": "Whisper tiny English (Workers AI)",
    "cf-deepgram-nova-3": "Deepgram Nova-3 (Workers AI)",
    "youtube-auto": "YouTube auto captions",
    "wayin": "Wayin",
    "fake": "Fake (test)",
}

# Providers that never send audio anywhere. Used only when run_meta.json does not say.
LOCAL_PROVIDERS = {"whisper-local", "whisper-local-turbo", "youtube-auto", "wayin", "fake"}

_SOURCE_RE = re.compile(r"\s*\(source: (?P<src>[^()]*)\)\.?\s*$")


# ---------------------------------------------------------------- helpers


def web_dir(settings: Settings) -> Path:
    return Path(os.environ.get("CLIPBENCH_WEB_DIR", settings.root / "web"))


def data_dir(settings: Settings) -> Path:
    return web_dir(settings) / "public" / "data"


def clean(value: Any) -> Any:
    """Make a value JSON safe: NaN and NA become null, numpy scalars become Python ones."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        return clean(value.tolist())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return [clean(r) for r in df.astype(object).to_dict("records")]


def _tags(raw: Any) -> list[str]:
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return [t for t in raw.split(",") if t]
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _num(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# ---------------------------------------------------------------- sections


def metrics_section(data: RunData) -> list[dict[str, Any]]:
    present = list(dict.fromkeys(str(m) for m in data.all_aggregates["metric"].dropna()))
    try:
        from clipbench.scoring.normalize import metrics_for

        order = [s.name for s in metrics_for(data.language)]
    except Exception:  # noqa: BLE001 - unknown language: keep the aggregates order
        order = []
    names = [m for m in order if m in present] + [m for m in present if m not in order]
    names.sort(key=lambda m: m != data.metric)
    return [{"name": m, "primary": m == data.metric, "label": METRIC_LABELS.get(m, m)} for m in names]


def providers_section(data: RunData) -> list[dict[str, Any]]:
    toml = _pricing_toml()
    raw = data.meta.get("providers") or []
    if isinstance(raw, dict):
        raw = [{"id": k, **(v if isinstance(v, dict) else {"model_id": v})} for k, v in raw.items()]
    meta_by_id = {str(p.get("id") or p.get("provider")): p for p in raw if isinstance(p, dict)}
    overall = data.all_aggregates[data.all_aggregates["slice_kind"] == "all"]
    agg_models = {
        str(r.provider): str(r.model_id)
        for r in overall[["provider", "model_id"]].drop_duplicates().itertuples()
    }
    ids = list(dict.fromkeys([*meta_by_id, *agg_models]))
    ids = [i for i in ids if i in agg_models] or ids  # only providers that have numbers

    out = []
    for pid in ids:
        m = meta_by_id.get(pid, {})
        price = toml.get(pid, {})
        model_id = m.get("model_id") or agg_models.get(pid) or price.get("model_id")
        per_min = _num(m.get("usd_per_minute"))
        if per_min is None:
            per_min = _num((price.get("models") or {}).get(model_id, price.get("usd_per_minute")))
        usd_per_hour = None if per_min is None else round(per_min * 60, 6)
        paid = m.get("paid")
        if paid is None:
            paid = bool(usd_per_hour) and pid not in LOCAL_PROVIDERS
        uploads = m.get("uploads_audio")
        if uploads is None:
            uploads = pid not in LOCAL_PROVIDERS
        out.append(
            {
                "id": pid,
                "model_id": model_id,
                "label": PROVIDER_LABELS.get(pid, pid),
                "pricing_checked_on": m.get("pricing_checked_on")
                or m.get("checked_on")
                or price.get("checked_on"),
                "usd_per_hour": usd_per_hour,
                "paid": bool(paid),
                "uploads_audio": bool(uploads),
                **policy_fields(price),
            }
        )
    return out


def policy_fields(price: dict[str, Any]) -> dict[str, Any]:
    """Data policy from pricing.toml. A key missing there is unknown and exported as null.
    `opt_out` is what clipbench did (applied | not_offered | not_needed); `opt_out_note` is the free text."""
    return {
        "retention_days": price.get("retention_days"),
        "trains_by_default": price.get("trains_by_default"),
        "opt_out": price.get("opt_out_status"),
        "opt_out_note": price.get("opt_out"),
        "policy_url": price.get("policy_url"),
    }


def split_source(line: str) -> tuple[str, str]:
    """'DRAFT: verify. X (source: t, k=v).' -> ('DRAFT: verify. X.', 't, k=v')."""
    m = _SOURCE_RE.search(line)
    if not m:
        return line, ""
    text = line[: m.start()].rstrip()
    if not text.endswith((".", ":", ")")):
        text += "."
    return text, m.group("src").strip()


def findings_section(data: RunData, tiers: list[str]) -> list[dict[str, str]]:
    """Legacy draft lines for the run page. Empty unless the language is published (rules.py)."""
    if not data.published:
        return []
    out = []
    for tier in tiers:
        for line in findings_mod.draft_findings(
            data.aggregates, data.paired, data.stability, tier, data.metric, eligible=data.eligible
        ):
            text, source = split_source(line)
            out.append({"tier": tier, "text": text, "source": source})
    return out


def errors_section(errors: dict | None) -> dict[str, Any]:
    errors = errors or {}
    providers = {}
    for pid, info in sorted((errors.get("providers") or {}).items()):
        info = info or {}
        providers[pid] = {
            "top_substitutions": [list(t) for t in info.get("top_substitutions", [])],
            "worst_clips": [
                {
                    k: (_audio(w.get(k)) if k == "audio_condition" else w.get(k))
                    for k in ("video_id", "error_rate", "others_median", "category", "audio_condition")
                    if k in w
                }
                for w in info.get("worst_clips", [])
            ],
        }
    examples = [
        {k: e.get(k) for k in ("provider", "video_id", "ref_snippet", "hyp_snippet")}
        for e in errors.get("examples", [])
    ]
    out: dict[str, Any] = {"providers": providers, "examples": examples}
    # Clips every provider failed (usually a bad reference); ids and the threshold only, no text.
    if "all_fail" in errors:
        out["all_fail"] = sorted(str(v) for v in errors.get("all_fail") or [])
        out["all_fail_threshold"] = errors.get("all_fail_threshold")
    return clean(out)


def _channels(settings: Settings, run_id: str) -> dict[str, str]:
    path = settings.runs_dir / run_id / "rows.parquet"
    if not path.exists():
        return {}
    try:
        rows = pd.read_parquet(path, columns=["video_id", "channel"])
    except Exception:  # noqa: BLE001 - older runs without a channel column
        return {}
    rows = rows.dropna()
    return {str(r.video_id): str(r.channel) for r in rows.drop_duplicates("video_id").itertuples()}


def clips_section(data: RunData, settings: Settings) -> list[dict[str, Any]]:
    scores = data.scores
    if scores is None or scores.empty:
        return []
    channels = _channels(settings, data.run_id)
    cols = [
        c for c in ("video_id", "tier", "duration_s", "tags", "category", "audio_condition", "conditions")
        if c in scores
    ]
    uniq = scores[cols].drop_duplicates(subset=["video_id", "tier"]).sort_values(["tier", "video_id"])
    out = []
    for r in uniq.astype(object).to_dict("records"):
        out.append(
            clean(
                {
                    "video_id": str(r["video_id"]),
                    "tier": r.get("tier"),
                    "duration_s": _num(r.get("duration_s")),
                    "channel": channels.get(str(r["video_id"])),
                    "tags": _tags(r.get("tags")),
                    **clip_kind(str(r["video_id"])),
                    "category": r.get("category"),
                    "audio_condition": _audio(r.get("audio_condition")),
                    "conditions": _tags(r.get("conditions")),
                }
            )
        )
    return out


def _audio(value: Any) -> str | None:
    """'music' or 'no_music'; None when nobody listened (an unreviewed reference is 'unknown' in scores)."""
    return str(value) if value in ("music", "no_music") else None


def clip_kind(clip_id: str) -> dict[str, Any]:
    m = _WINDOW_RE.match(clip_id)
    if not m:
        return {"kind": "short", "start_s": None}
    return {"kind": "window", "start_s": int(m.group("start")) / 1000}


def per_clip_section(data: RunData) -> list[dict[str, Any]]:
    scores = data.scores
    if scores is None or scores.empty:
        return []
    keep = ["provider", "video_id", "tier", "metric", "error_rate", "hallucination_rate", "latency_ms"]
    df = scores[[c for c in keep if c in scores]].copy()
    if "latency_ms" in df:
        # Same rule as the aggregates: a cache hit's latency belongs to an earlier run, and 0 means no request.
        lat = pd.to_numeric(df["latency_ms"], errors="coerce")
        stale = lat.isna() | (lat <= 0)
        if "cached" in scores:
            stale |= scores["cached"].fillna(False).astype(bool)
        df["latency_ms"] = lat.where(~stale)
    df = df.sort_values(["provider", "tier", "metric", "video_id"])
    return records(df)


def tiers_of(data: RunData) -> list[str]:
    present = set(data.all_aggregates["tier"].dropna().astype(str))
    return [t for t in TIERS if t in present] + sorted(present - set(TIERS))


def n_clips_by_tier(data: RunData, tiers: list[str]) -> dict[str, int]:
    overall = data.all_aggregates[
        (data.all_aggregates["slice_kind"] == "all") & (data.all_aggregates["metric"] == data.metric)
    ]
    out = {}
    for tier in tiers:
        grp = overall[overall["tier"] == tier]
        if len(grp):
            out[tier] = int(grp["n_clips"].max())
    return out


# ---------------------------------------------------------------- build


def build_run(run_id: str, settings: Settings) -> dict[str, Any]:
    data = load_run(run_id, settings)
    tiers = tiers_of(data)
    meta = data.meta
    return {
        "run_id": run_id,
        "created_at": meta.get("created_at") or meta.get("updated_at"),
        "language": data.language,
        "git_commit": meta.get("git_commit") or meta.get("commit"),
        "sample_seed": meta.get("sample_seed", meta.get("seed")),
        "metrics": metrics_section(data),
        "providers": providers_section(data),
        "aggregates": records(rules.annotate_aggregates(data.all_aggregates, data.status)),
        "paired": records(data.paired),
        "ranking": rules.ranking_section(data.all_aggregates, data.status),
        "stability": records(data.stability),
        "findings": findings_section(data, tiers),
        "errors": errors_section(data.errors),
        "clips": clips_section(data, settings),
        "per_clip": per_clip_section(data),
        "limitations": list(limitations(data).items),
        "languages": [status_dict(data)],
        "run_meta": run_meta_section(meta),
        **v3_sections(run_id, settings),
    }


# v3 feature modules (docs/V3.md) each expose export_section(run_id, settings) -> dict | None, whose keys
# are merged into the run JSON. A module that is not built yet, or has no data for this run, adds nothing;
# every V3 key is always present (null when absent) so the site can tell "no data" from "old export".
V3_KEYS = [
    "streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform", "compute",
    "diarization", "lid", "entities", "style", "reliability", "captions", "features",
]
V3_HOOKS = [
    "clipbench.streaming.export",
    "clipbench.scoring.export_v3_quality",
    "clipbench.scoring.semantic",
    "clipbench.scoring.formatting",
    "clipbench.scoring.entities",
    "clipbench.scoring.style",
    "clipbench.providers.export_v3",
    "clipbench.scoring.diarization",
    "clipbench.scoring.lid",
    "clipbench.scoring.captions",
    "clipbench.sweeps.export",
    "clipbench.longform",
    "clipbench.compute",
    "clipbench.canary",
    "clipbench.scoring.reliability",
]


# Whole-word credential names; "keyterms" and "settings_hash" are not secrets.
_SECRETISH = ("api_key", "apikey", "access_key", "secret", "token", "password", "authorization", "credential")


def run_meta_section(meta: dict[str, Any]) -> dict[str, Any]:
    """The exact settings a run used, for the model pages: kind, run options, and per provider model id
    and settings. Anything that looks like a credential is dropped."""

    def safe(d: Any) -> Any:
        if isinstance(d, dict):
            return {
                k: safe(v)
                for k, v in d.items()
                if not any(s in str(k).lower().replace("-", "_") for s in _SECRETISH)
            }
        if isinstance(d, list):
            return [safe(v) for v in d]
        return d

    providers = {}
    for p in meta.get("providers") or []:
        if isinstance(p, dict) and p.get("id"):
            providers[p["id"]] = safe(
                {k: p.get(k) for k in ("model_id", "settings", "settings_hash", "usd_per_minute", "pricing_checked_on")}
            )
    return clean(
        {
            "kind": meta.get("kind", "batch"),
            "options": safe(meta.get("options") or {}),
            "providers": providers,
            "machine": safe(meta.get("machine")),
            "clipbench_version": meta.get("clipbench_version"),
            "packages": meta.get("packages"),
            # how latency was measured; contended for runs recorded before providers ran one at a time
            "latency_mode": latency_mode(meta),
        }
    )


def v3_sections(run_id: str, settings: Settings) -> dict[str, Any]:
    import importlib
    import logging

    out: dict[str, Any] = dict.fromkeys(V3_KEYS)
    for name in V3_HOOKS:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name and name.startswith(exc.name):
                continue  # not built yet
            raise
        hook = getattr(module, "export_section", None)
        if hook is None:
            continue
        try:
            section = hook(run_id, settings)
        except Exception as exc:  # one broken feature must not block the whole export
            logging.getLogger(__name__).warning("export hook %s failed: %s", name, exc)
            continue
        for key, value in (section or {}).items():
            if value is not None:
                out[key] = clean(value)
    if isinstance(out.get("features"), list):
        for rec in out["features"]:
            if isinstance(rec, dict) and rec.get("provider"):
                rec.setdefault("label", PROVIDER_LABELS.get(rec["provider"], rec["provider"]))
    return out


def status_dict(data: RunData) -> dict[str, Any]:
    st = data.status or rules.LanguageStatus(lang=data.language)
    return clean(st.as_dict())


def index_entry(run: dict[str, Any], tiers: list[str], n_clips: dict[str, int]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "created_at": run["created_at"],
        "language": run["language"],
        "tiers": tiers,
        "n_clips": n_clips,
        "providers": [p["id"] for p in run["providers"]],
        "languages": run.get("languages", []),
    }


def status_runs(runs: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Per language, the run whose status the site shows: (run_id, status dict).

    The newest run with verified clips wins; a language with none falls back to its newest run. So a
    later creator-tier pipeline check never replaces the verified progress counts or findings.
    """
    chosen: dict[str, tuple[str, dict[str, Any]]] = {}
    newest_first = sorted(runs, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    for want_verified in (True, False):
        for r in newest_first:
            for st in r.get("languages") or []:
                if not isinstance(st, dict) or not st.get("lang") or st["lang"] in chosen:
                    continue
                if want_verified and not (st.get("verified_clips") or 0) > 0:
                    continue
                chosen[st["lang"]] = (str(r.get("run_id")), st)
    return chosen


def index_languages(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Status per v2 language from status_runs() (zeros when a language has no run)."""
    latest: dict[str, rules.LanguageStatus] = {}
    for lang, (_, st) in status_runs(runs).items():
        fields = {k: st.get(k) for k in rules.LanguageStatus.__dataclass_fields__ if k in st}
        latest[lang] = rules.LanguageStatus(**fields)
    return clean(rules.all_languages(latest))


def findings_file(data: RunData) -> dict[str, Any]:
    """findings/<lang>.json: structured findings for a published language, none otherwise."""
    items = []
    if data.published:
        items = findings_mod.structured_findings(
            data.aggregates,
            data.paired,
            data.metric,
            tier="verified",
            min_clips=rules.MIN_SLICE_CLIPS,
            eligible=data.eligible,
        )
    keys = ("id", "slug", "text", "status", "source", "numbers", "cluster_sensitive")
    return clean(
        {
            "lang": data.language,
            "run_id": data.run_id,
            "created_at": data.meta.get("created_at") or data.meta.get("updated_at"),
            "published": data.published,
            "findings": [{k: f[k] for k in keys} for f in items],
        }
    )


def write_findings(data: RunData, settings: Settings, runs: list[dict[str, Any]] | None = None) -> Path:
    """Write findings/<lang>.json for this run's language; other v2 languages get an empty file if missing.

    With `runs` (the index), the file is only replaced when this run is the one status_runs() picks for
    its language, so exporting an older or creator-only run keeps the verified run's findings."""
    out = data_dir(settings) / "findings"
    path = out / f"{data.language}.json"
    chosen = status_runs(runs).get(data.language) if runs is not None else None
    if chosen is None or chosen[0] == data.run_id or not path.exists():
        _write_json(findings_file(data), path)
    for lang in rules.LANGUAGES:
        other = out / f"{lang}.json"
        if not other.exists() or json.loads(other.read_text() or "{}").get("run_id") is None:
            empty = {"lang": lang, "run_id": None, "created_at": None, "published": False, "findings": []}
            _write_json(empty, other)
    return path


def _write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=1) + "\n")
    tmp.replace(path)


def export_run(run_id: str, settings: Settings | None = None) -> tuple[Path, Path]:
    """Write runs/<run_id>.json and merge the run into index.json. Returns both paths."""
    settings = settings or get_settings()
    from clipbench.runs import resolve_run_id

    run_id = resolve_run_id(run_id, settings)
    data = load_run(run_id, settings)
    tiers = tiers_of(data)
    run = build_run(run_id, settings)
    out = data_dir(settings)
    run_path = out / "runs" / f"{run_id}.json"
    _write_json(run, run_path)

    index_path = out / "index.json"
    runs: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            old = json.loads(index_path.read_text())
            if not old.get("demo"):  # never mix demo runs with real ones
                runs = [r for r in old.get("runs", []) if isinstance(r, dict) and r.get("run_id") != run_id]
        except ValueError:
            runs = []
    runs.append(index_entry(run, tiers, n_clips_by_tier(data, tiers)))
    runs.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("run_id"))), reverse=True)
    # A real export replaces the demo flag: index.json now points at real runs only.
    _write_json({"latest": runs[0]["run_id"], "runs": runs, "languages": index_languages(runs)}, index_path)
    findings_path = write_findings(data, settings, runs)
    try:
        from clipbench.report import og

        og.write_all(data, json.loads(findings_path.read_text()), index_languages(runs), web_dir(settings))
    except Exception as exc:  # noqa: BLE001 - images are a nice to have; the data export must not fail
        typer.echo(f"og images skipped: {exc}", err=True)
    return run_path, index_path


def register(app: typer.Typer) -> None:
    @app.command("export")
    def export(run_id: str = typer.Argument(..., help="Run id under results/, or 'latest'.")) -> None:
        """Write web/public/data/index.json and web/public/data/runs/<run_id>.json for the web app."""
        try:
            run_path, index_path = export_run(run_id)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"wrote {run_path}")
        typer.echo(f"wrote {index_path}")
        typer.echo("next: clipbench publish --check")
