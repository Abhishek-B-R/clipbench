"""B21 caption export check: what a caption app would ship, built from each provider's words.

One fixed line break rule (`build_cues`) turns timed words into cues, for the provider and for the
reference words (creator timing or `clipbench align`), so the two are comparable:

- A cue holds at most MAX_LINES (2) lines of at most MAX_CHARS[language] characters each
  (42 for en, es, hi; 16 for ja and ko, the common broadcast and streaming limits).
- A new cue starts before a word when: the pause since the previous word is at least PAUSE_MS
  (700 ms); or the previous word ends a sentence (. ? ! and their full width forms); or the cue would
  last longer than MAX_CUE_MS (7000 ms); or its text would no longer fit in two lines.
- A cue whose text is longer than one line is split into two lines at the word boundary closest to
  the middle, preferring a boundary after a comma when one is within a quarter of the text.
- Cue start is the first word's start and cue end the last word's end. Words are joined with a
  space, except Japanese (no spaces).

Scores per clip (provider cues against reference cues):
- timing: for each reference cue, the provider cue with the largest time overlap; absolute start and
  end error in ms (median per clip); `overlap_ratio` is the time intersection over union of all
  provider cue time against all reference cue time.
- readability: characters per second per cue against CPS_LIMIT[language] (17 for en, es, hi; 4 for
  ja; 12 for ko), counted without spaces for ja and ko; lines longer than MAX_CHARS; and
  `cue_count_ratio` = provider cues / reference cues.

Writes results/<run_id>/captions.parquet and captions_aggregates.parquet (numbers only). SRT and VTT
files contain caption text, so `write_caption_files` writes them under data/captions/<run_id>/ only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import Word
from clipbench.scoring.stats import bootstrap_ci
from clipbench.scoring.v3_inputs import TOO_SMALL, load_run, model_ids, records, slices

MAX_LINES = 2
MAX_CHARS = {"en": 42, "es": 42, "fr": 42, "hi": 42, "ja": 16, "ko": 16}
DEFAULT_MAX_CHARS = 42
CPS_LIMIT = {"en": 17.0, "es": 17.0, "fr": 17.0, "hi": 17.0, "ja": 4.0, "ko": 12.0}
DEFAULT_CPS_LIMIT = 17.0
MAX_CUE_MS = 7000
PAUSE_MS = 700
SENTENCE_END = tuple(".?!。？！…")
UNSPACED = {"ja"}
NO_SPACE_COUNT = {"ja", "ko"}

CLIP_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "conditions",
    "n_ref_cues", "n_hyp_cues", "cue_count_ratio", "n_matched",
    "start_error_median_ms", "end_error_median_ms", "intersection_ms", "union_ms", "overlap_ratio",
    "cps_median", "cps_over_limit", "n_lines", "line_over_limit", "ref_cps_over_limit",
]
AGG_COLUMNS = [
    "provider", "model_id", "tier", "language", "slice_kind", "slice", "n_clips", "too_small",
    "start_error_median_ms", "end_error_median_ms",
    "overlap_ratio", "overlap_ci_low", "overlap_ci_high",
    "cps_over_limit_rate", "cps_ci_low", "cps_ci_high",
    "line_over_limit_rate", "line_ci_low", "line_ci_high",
    "ref_cps_over_limit_rate", "cue_count_ratio",
]


def _lang(language: str) -> str:
    return str(language).split("-")[0].lower()


def max_chars(language: str) -> int:
    return MAX_CHARS.get(_lang(language), DEFAULT_MAX_CHARS)


def cps_limit(language: str) -> float:
    return CPS_LIMIT.get(_lang(language), DEFAULT_CPS_LIMIT)


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    lines: list[str]

    @property
    def duration_ms(self) -> int:
        return max(self.end_ms - self.start_ms, 0)


def _join(tokens: Sequence[str], language: str) -> str:
    return ("" if _lang(language) in UNSPACED else " ").join(t for t in tokens if t)


def split_lines(tokens: Sequence[str], language: str) -> list[str]:
    """One line if it fits, else two lines split at the boundary closest to the middle."""
    text = _join(tokens, language)
    limit = max_chars(language)
    if len(text) <= limit or len(tokens) < 2:
        return [text]
    best_i, best_score = 1, None
    total = len(text)
    for i in range(1, len(tokens)):
        a, b = _join(tokens[:i], language), _join(tokens[i:], language)
        over = max(len(a) - limit, 0) + max(len(b) - limit, 0)
        balance = abs(len(a) - len(b))
        comma = tokens[i - 1].endswith((",", "、", "，")) and balance <= total / 4
        score = (over, 0 if comma else 1, balance)
        if best_score is None or score < best_score:
            best_i, best_score = i, score
    return [_join(tokens[:best_i], language), _join(tokens[best_i:], language)]


def build_cues(words: Sequence[Word] | None, language: str) -> list[Cue]:
    """Cues from timed words with the fixed rule in the module docstring."""
    if not words:
        return []
    limit = max_chars(language) * MAX_LINES
    ws = sorted((w for w in words if w.text.strip()), key=lambda w: w.start_ms)
    cues: list[Cue] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            tokens = [w.text.strip() for w in current]
            cues.append(Cue(current[0].start_ms, max(w.end_ms for w in current), split_lines(tokens, language)))
            current.clear()

    for w in ws:
        if current:
            prev = current[-1]
            text = _join([x.text.strip() for x in [*current, w]], language)
            if (
                w.start_ms - prev.end_ms >= PAUSE_MS
                or prev.text.strip().endswith(SENTENCE_END)
                or max(w.end_ms, prev.end_ms) - current[0].start_ms > MAX_CUE_MS
                or len(text) > limit
            ):
                flush()
        current.append(w)
    flush()
    return cues


# ---------------------------------------------------------------- SRT / VTT


def _ts(ms: int, sep: str) -> str:
    ms = max(int(ms), 0)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: Sequence[Cue]) -> str:
    blocks = [
        f"{i}\n{_ts(c.start_ms, ',')} --> {_ts(c.end_ms, ',')}\n" + "\n".join(c.lines)
        for i, c in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(cues: Sequence[Cue]) -> str:
    blocks = [f"{_ts(c.start_ms, '.')} --> {_ts(c.end_ms, '.')}\n" + "\n".join(c.lines) for c in cues]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


# ---------------------------------------------------------------- scoring


def _chars(cue: Cue, language: str) -> int:
    text = "".join(cue.lines)
    if _lang(language) in NO_SPACE_COUNT:
        text = "".join(text.split())
    return len(text)


def cue_cps(cue: Cue, language: str) -> float:
    seconds = max(cue.duration_ms, 1) / 1000
    return _chars(cue, language) / seconds


def _union(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _length(intervals: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in intervals)


def _intersection(x: list[tuple[int, int]], y: list[tuple[int, int]]) -> int:
    total, i, j = 0, 0, 0
    while i < len(x) and j < len(y):
        lo, hi = max(x[i][0], y[j][0]), min(x[i][1], y[j][1])
        total += max(hi - lo, 0)
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return total


@dataclass
class CaptionScore:
    n_ref_cues: int
    n_hyp_cues: int
    n_matched: int
    start_error_median_ms: float | None
    end_error_median_ms: float | None
    intersection_ms: int
    union_ms: int
    cps_median: float | None
    cps_over_limit: int
    n_lines: int
    line_over_limit: int
    ref_cps_over_limit: int

    @property
    def overlap_ratio(self) -> float | None:
        return self.intersection_ms / self.union_ms if self.union_ms else None

    @property
    def cue_count_ratio(self) -> float | None:
        return self.n_hyp_cues / self.n_ref_cues if self.n_ref_cues else None


def score_cues(ref: Sequence[Cue], hyp: Sequence[Cue], language: str) -> CaptionScore:
    starts, ends = [], []
    for r in ref:
        best, best_ov = None, 0
        for h in hyp:
            ov = min(r.end_ms, h.end_ms) - max(r.start_ms, h.start_ms)
            if ov > best_ov:
                best, best_ov = h, ov
        if best is not None:
            starts.append(abs(best.start_ms - r.start_ms))
            ends.append(abs(best.end_ms - r.end_ms))
    ru = _union([(c.start_ms, c.end_ms) for c in ref])
    hu = _union([(c.start_ms, c.end_ms) for c in hyp])
    inter = _intersection(ru, hu)
    limit = cps_limit(language)
    cps = [cue_cps(c, language) for c in hyp]
    lines = [line for c in hyp for line in c.lines]
    return CaptionScore(
        n_ref_cues=len(ref),
        n_hyp_cues=len(hyp),
        n_matched=len(starts),
        start_error_median_ms=float(np.median(starts)) if starts else None,
        end_error_median_ms=float(np.median(ends)) if ends else None,
        intersection_ms=inter,
        union_ms=_length(ru) + _length(hu) - inter,
        cps_median=float(np.median(cps)) if cps else None,
        cps_over_limit=sum(v > limit for v in cps),
        n_lines=len(lines),
        line_over_limit=sum(len(line) > max_chars(language) for line in lines),
        ref_cps_over_limit=sum(cue_cps(c, language) > limit for c in ref),
    )


def score_frame(run_id: str, settings: Settings) -> pd.DataFrame:
    inputs = load_run(run_id, settings)
    if inputs is None:
        return pd.DataFrame(columns=CLIP_COLUMNS)
    out = []
    for row in inputs.main_rows().itertuples(index=False):
        ref = inputs.ref(row.video_id, row.tier)
        hyp = inputs.hyp(row.provider, row.video_id)
        if ref is None or not ref.words or hyp is None or not hyp.words:
            continue  # needs word timings on both sides
        ref_cues = build_cues(ref.words, row.language)
        hyp_cues = build_cues(hyp.words, row.language)
        if not ref_cues:
            continue
        sc = score_cues(ref_cues, hyp_cues, row.language)
        out.append(
            {
                "provider": row.provider,
                "model_id": row.model_id,
                "video_id": row.video_id,
                "tier": row.tier,
                "language": row.language,
                "conditions": json.dumps(ref.conditions),
                "n_ref_cues": sc.n_ref_cues,
                "n_hyp_cues": sc.n_hyp_cues,
                "cue_count_ratio": sc.cue_count_ratio,
                "n_matched": sc.n_matched,
                "start_error_median_ms": sc.start_error_median_ms,
                "end_error_median_ms": sc.end_error_median_ms,
                "intersection_ms": sc.intersection_ms,
                "union_ms": sc.union_ms,
                "overlap_ratio": sc.overlap_ratio,
                "cps_median": sc.cps_median,
                "cps_over_limit": sc.cps_over_limit,
                "n_lines": sc.n_lines,
                "line_over_limit": sc.line_over_limit,
                "ref_cps_over_limit": sc.ref_cps_over_limit,
            }
        )
    return pd.DataFrame(out, columns=CLIP_COLUMNS)


def _rate(num: float, den: float) -> float:
    return float(num / den) if den else float("nan")


def aggregate(clips: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    if clips.empty:
        return pd.DataFrame(columns=AGG_COLUMNS)
    recs = []
    for (tier, language), part in clips.groupby(["tier", "language"], sort=True):
        for kind, name, mask in slices(part):
            for provider, g in part[mask].groupby("provider", sort=True):
                ov = bootstrap_ci(g["intersection_ms"].to_numpy(), g["union_ms"].to_numpy(), seed=seed)
                cps = bootstrap_ci(g["cps_over_limit"].to_numpy(), g["n_hyp_cues"].to_numpy(), seed=seed)
                ln = bootstrap_ci(g["line_over_limit"].to_numpy(), g["n_lines"].to_numpy(), seed=seed)
                has_cues = g["n_hyp_cues"].sum() > 0
                has_lines = g["n_lines"].sum() > 0
                nan = float("nan")
                recs.append(
                    {
                        "provider": provider,
                        "model_id": model_ids(g),
                        "tier": tier,
                        "language": language,
                        "slice_kind": kind,
                        "slice": name,
                        "n_clips": int(len(g)),
                        "too_small": bool(len(g) < TOO_SMALL),
                        # medians across clips of the per clip median
                        "start_error_median_ms": float(g["start_error_median_ms"].median()),
                        "end_error_median_ms": float(g["end_error_median_ms"].median()),
                        "overlap_ratio": ov.rate if g["union_ms"].sum() else nan,
                        "overlap_ci_low": ov.ci_low if g["union_ms"].sum() else nan,
                        "overlap_ci_high": ov.ci_high if g["union_ms"].sum() else nan,
                        "cps_over_limit_rate": cps.rate if has_cues else nan,
                        "cps_ci_low": cps.ci_low if has_cues else nan,
                        "cps_ci_high": cps.ci_high if has_cues else nan,
                        "line_over_limit_rate": ln.rate if has_lines else nan,
                        "line_ci_low": ln.ci_low if has_lines else nan,
                        "line_ci_high": ln.ci_high if has_lines else nan,
                        "ref_cps_over_limit_rate": _rate(g["ref_cps_over_limit"].sum(), g["n_ref_cues"].sum()),
                        "cue_count_ratio": _rate(g["n_hyp_cues"].sum(), g["n_ref_cues"].sum()),
                    }
                )
    return pd.DataFrame(recs, columns=AGG_COLUMNS)


def score_captions(run_id: str, settings: Settings | None = None) -> Path | None:
    """Write captions.parquet and captions_aggregates.parquet; None when no clip has word timings
    on both sides."""
    s = settings or get_settings()
    clips = score_frame(run_id, s)
    if clips.empty:
        return None
    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    clips.to_parquet(out / "captions.parquet", index=False)
    aggregate(clips, s.sample_seed).to_parquet(out / "captions_aggregates.parquet", index=False)
    return out / "captions.parquet"


def write_caption_files(run_id: str, settings: Settings | None = None) -> Path | None:
    """SRT and VTT per provider and clip under data/captions/<run_id>/<provider>/ (text: never public)."""
    s = settings or get_settings()
    inputs = load_run(run_id, s)
    if inputs is None:
        return None
    root = s.data_dir / "captions" / run_id
    for row in inputs.main_rows().itertuples(index=False):
        hyp = inputs.hyp(row.provider, row.video_id)
        if hyp is None or not hyp.words:
            continue
        cues = build_cues(hyp.words, row.language)
        d = root / str(row.provider)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{row.video_id}.srt").write_text(to_srt(cues), encoding="utf-8")
        (d / f"{row.video_id}.vtt").write_text(to_vtt(cues), encoding="utf-8")
    return root


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    agg_path = settings.results_dir / run_id / "captions_aggregates.parquet"
    if not agg_path.exists() and score_captions(run_id, settings) is None:
        return None
    aggs = records(pd.read_parquet(agg_path)) if agg_path.exists() else None
    if not aggs:
        return None
    return {
        "captions": {
            "rule": {
                "max_lines": MAX_LINES,
                "max_chars": MAX_CHARS,
                "max_cue_ms": MAX_CUE_MS,
                "pause_ms": PAUSE_MS,
                "cps_limit": CPS_LIMIT,
            },
            "aggregates": aggs,
        }
    }
