"""`clipbench estimate`: what one complete run of everything would cost. No network, no keys needed.

Prices every paid track for N clips per language: the batch run with its stability reruns, the extra
batch passes that some metrics need (language detection, keyterms, diarization), the streaming track,
the SNR sweeps, long-form, the semantic judge, and a month of canaries. Every number comes from the same
estimate functions the real commands use (pricing.toml), so it is an upper bound, not a quote.
Assumptions are printed with the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

import typer
from rich.console import Console
from rich.table import Table

from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Reference

LANGS = ["en", "ja", "ko", "es", "hi"]


@dataclass(frozen=True)
class Assumptions:
    clips_per_lang: int = 150
    diarize_share: float = 0.3  # share of clips with several speakers (diarization pass)
    sweep_clips: int = 30
    sweep_snrs: int = 5
    sweep_bed_kinds: int = 2  # music and noise
    sweep_pad_s: float = 4.0  # 2 s of bed before and after each clip
    longform_videos: int = 20
    longform_minutes: float = 10.0
    judge_chars: int = 300  # reference and hypothesis length per pair, upper bound
    canary_clips: int = 30
    canary_runs_per_month: float = 4.35  # weekly
    default_clip_s: float = 40.0


def _durations(settings: Settings, n: int, default: float) -> list[float]:
    """Real clip lengths from the sample when there is one, cycled to n; else a flat default."""
    import pandas as pd

    durs: list[float] = []
    if settings.sample_path.exists():
        try:
            df = pd.read_parquet(settings.sample_path)
            durs = [float(x) for x in pd.to_numeric(df.get("duration_s"), errors="coerce").dropna() if x > 0]
        except Exception:
            durs = []
    if not durs:
        return [default] * n
    return [durs[i % len(durs)] for i in range(n)]


def _clips(lang: str, durations: list[float], prefix: str = "est") -> list[Clip]:
    out = []
    for i, d in enumerate(durations):
        vid = f"{prefix}{lang}{i:05d}"
        ref = Reference(video_id=vid, language=lang, tier="verified", text="")
        out.append(Clip(video_id=vid, language=lang, duration_s=d, audio_path="", audio_sha256=vid, reference=ref))
    return out


def _batch_providers(**options):
    from clipbench.budget import is_billable
    from clipbench.providers import registry

    out = []
    for pid in registry.ALL:
        try:
            p = registry.make(pid)
        except Exception:
            continue
        if options:
            p.options.update(options)
        if is_billable(p):
            out.append(p)
    return out


def _supports(p, option: str) -> bool:
    return option in getattr(p, "supported_options", frozenset())


def estimate_all(settings: Settings | None = None, a: Assumptions = Assumptions()) -> list[tuple[str, str, float]]:
    """Rows of (track, detail, usd) for every language together."""
    from clipbench.budget import estimate, supports_language

    s = settings or get_settings()
    rows: list[tuple[str, str, float]] = []
    durs = _durations(s, a.clips_per_lang, a.default_clip_s)
    batch = _batch_providers()
    all_clips = {lang: _clips(lang, durs) for lang in LANGS}

    def add(track: str, detail: str, usd: float) -> None:
        rows.append((track, detail, usd))

    # 1. Batch, with the stability reruns.
    def langs(ps: list, lang: str) -> list:
        return [p for p in ps if supports_language(p, lang)]

    usd = sum(estimate(langs(batch, lang), c).total_usd for lang, c in all_clips.items())
    add("batch", f"{len(batch)} paid providers x {a.clips_per_lang} clips x {len(LANGS)} languages (each provider "
        "only on languages it supports), plus stability reruns", usd)

    # 2. Extra batch passes for metrics that need different settings (no stability reruns).
    lid = [p for p in batch if _supports(p, "detect_language")]
    usd = sum(estimate(langs(lid, lang), c, stability_n=0).total_usd for lang, c in all_clips.items())
    add("language ID pass", f"{len(lid)} providers with detection, every clip again", usd)
    kt = [p for p in batch if _supports(p, "keyterms")]
    usd = sum(estimate(langs(kt, lang), c, stability_n=0).total_usd for lang, c in all_clips.items())
    add("keyterms pass", f"{len(kt)} providers with custom vocabulary, every clip again", usd)
    dz = [p for p in batch if _supports(p, "diarize")]
    n_dz = max(1, round(a.clips_per_lang * a.diarize_share))
    usd = sum(estimate(langs(dz, lang), c[:n_dz], stability_n=0).total_usd for lang, c in all_clips.items())
    add("diarization pass", f"{len(dz)} providers on {n_dz} multi-speaker clips per language", usd)

    # 3. Streaming track.
    try:
        from clipbench.budget import is_billable
        from clipbench.streaming import registry as sreg
        from clipbench.streaming.runner import estimate_stream

        stream = []
        for pid in sreg.ALL:
            try:
                p = sreg.make(pid)
            except Exception:
                continue
            if is_billable(p):
                stream.append(p)
        usd = sum(estimate_stream(stream, c).total_usd for c in all_clips.values())
        add("streaming", f"{len(stream)} realtime providers, every clip at real time pace", usd)
    except ImportError:
        add("streaming", "streaming module not installed", 0.0)

    # 4. SNR sweeps: clean clips mixed at each SNR with each bed kind, padded with bed only audio.
    sweep_durs = [d + a.sweep_pad_s for d in durs[: a.sweep_clips]] * (a.sweep_snrs * a.sweep_bed_kinds)
    usd = sum(estimate(langs(batch, lang), _clips(lang, sweep_durs, "sw"), stability_n=0).total_usd for lang in LANGS)
    add("SNR sweeps", f"{a.sweep_clips} clean clips x {a.sweep_snrs} SNRs x {a.sweep_bed_kinds} bed kinds", usd)

    # 5. Long-form.
    lf = [a.longform_minutes * 60.0] * a.longform_videos
    usd = sum(estimate(langs(batch, lang), _clips(lang, lf, "lf"), stability_n=0).total_usd for lang in LANGS)
    add("long-form", f"{a.longform_videos} videos of {a.longform_minutes:.0f} min per language", usd)

    # 6. Semantic judge: one call per (clip, provider) pair, including free providers' outputs.
    try:
        from clipbench.judge.client import DEFAULT_MODEL, estimate_pair_usd
        from clipbench.providers import registry

        per_pair = estimate_pair_usd(DEFAULT_MODEL, "x" * a.judge_chars, "x" * a.judge_chars)
        n_pairs = len(registry.ALL) * a.clips_per_lang * len(LANGS)
        add("semantic judge", f"{n_pairs} pairs on {DEFAULT_MODEL}", per_pair * n_pairs)
    except ImportError:
        add("semantic judge", "judge module not installed", 0.0)

    # 7. A month of canaries (their own monthly cap applies on top).
    can = _durations(s, a.canary_clips, a.default_clip_s)
    one = sum(estimate(langs(batch, lang), _clips(lang, can, "cn"), stability_n=0).total_usd for lang in LANGS)
    add("canaries, per month", f"{a.canary_clips} clips per language, weekly", one * a.canary_runs_per_month)
    return rows


def register(app: typer.Typer) -> None:
    @app.command("estimate")
    def estimate_cmd(
        clips: int = typer.Option(150, "--clips", help="Clips per language."),
    ) -> None:
        """Price one complete run of everything (batch, extra passes, streaming, sweeps, long-form,
        judge, a month of canaries) from pricing.toml. No network."""
        s = get_settings()
        a = Assumptions(clips_per_lang=clips)
        rows = estimate_all(s, a)
        durs = _durations(s, clips, a.default_clip_s)
        console = Console()
        t = Table(title=f"clipbench: one complete run of everything, {clips} clips x {len(LANGS)} languages")
        t.add_column("track")
        t.add_column("what")
        t.add_column("USD (upper bound)", justify="right")
        for track, detail, usd in rows:
            t.add_row(track, detail, f"${usd:,.2f}")
        total = sum(u for _, _, u in rows)
        t.add_row("[bold]total[/bold]", "one complete run plus one month of canaries", f"[bold]${total:,.2f}[/bold]")
        console.print(t)
        console.print(
            f"Clip length: mean {mean(durs):.1f} s from data/sample.parquet (other languages assumed the same). "
            f"Cache hits, free tiers (Workers AI 10,000 neurons a day) and the canary monthly cap "
            f"(${s.canary_budget_usd_per_month if hasattr(s, 'canary_budget_usd_per_month') else 2:.2f}) "
            f"would lower the real bill. Global cap today: ${s.budget_usd:.2f}."
        )
