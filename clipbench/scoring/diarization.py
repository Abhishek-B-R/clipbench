"""B15 speaker diarization: diarization error rate (DER) and speaker count accuracy.

Reference: `speaker_turns` from the review UI (start_ms, end_ms, speaker), labels local to the clip.
Hypothesis: the provider's words with per word speaker labels (`Result.speakers`).

Method (documented in METHODOLOGY.md):
- Hypothesis speech: each labelled word is an interval of its speaker; consecutive words of the same
  speaker are joined when the gap between them is at most HYP_JOIN_GAP_MS (word timings leave small
  gaps that are not real pauses). Words with no speaker label are dropped.
- Time is scored on a FRAME_MS grid. Frames within COLLAR_MS of any reference turn boundary are not
  scored (the usual 250 ms forgiveness collar, applied on both sides of each boundary).
- Per scored frame, with N_ref reference speakers and N_hyp hypothesis speakers active:
  missed = max(0, N_ref - N_hyp), false alarm = max(0, N_hyp - N_ref),
  confusion = min(N_ref, N_hyp) - N_correct, where N_correct counts reference speakers whose mapped
  hypothesis speaker is also active. The mapping is one to one and maximises total overlap
  (Hungarian assignment on the overlap matrix).
- DER = (missed + false alarm + confusion) / total reference speaker time, all in scored frames.
- Speaker count accuracy: the number of distinct labelled hypothesis speakers equals the number of
  distinct reference speakers.

Only clips with speaker_turns count. A provider is scored on a clip when it returned speaker labels,
or when it returned no words at all but labels speakers on other clips of the run (an empty output
from a diarizing provider is all missed speech, not a missing measurement).
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import SpeakerTurn, Word
from clipbench.scoring.stats import bootstrap_ci
from clipbench.scoring.v3_inputs import TOO_SMALL, load_run, model_ids, records, slices

log = logging.getLogger(__name__)

COLLAR_MS = 250
FRAME_MS = 10
HYP_JOIN_GAP_MS = 500

CLIP_COLUMNS = [
    "provider", "model_id", "video_id", "tier", "language", "conditions",
    "ref_speakers", "hyp_speakers", "speaker_count_correct",
    "scored_ms", "missed_ms", "false_alarm_ms", "confusion_ms", "der",
]
AGG_COLUMNS = [
    "provider", "model_id", "tier", "language", "slice_kind", "slice", "n_clips", "too_small",
    "der", "der_ci_low", "der_ci_high", "missed_rate", "false_alarm_rate", "confusion_rate",
    "speaker_count_acc", "speaker_count_ci_low", "speaker_count_ci_high",
]


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    speaker: str


@dataclass
class DiarizationScore:
    scored_ms: float
    missed_ms: float
    false_alarm_ms: float
    confusion_ms: float
    ref_speakers: int
    hyp_speakers: int
    mapping: dict[str, str]

    @property
    def der(self) -> float | None:
        if self.scored_ms <= 0:
            return None
        return (self.missed_ms + self.false_alarm_ms + self.confusion_ms) / self.scored_ms

    @property
    def speaker_count_correct(self) -> bool:
        return self.ref_speakers == self.hyp_speakers


def hypothesis_segments(
    words: Sequence[Word] | None, speakers: Sequence[str | None] | None, *, join_gap_ms: int = HYP_JOIN_GAP_MS
) -> list[Segment]:
    """Speaker segments from labelled words, same speaker neighbours joined across short gaps."""
    if not words or not speakers:
        return []
    labelled = sorted(
        ((w, s) for w, s in zip(words, speakers, strict=False) if s is not None and w.end_ms > w.start_ms),
        key=lambda ws: ws[0].start_ms,
    )
    out: list[Segment] = []
    for w, s in labelled:
        if out and out[-1].speaker == s and w.start_ms - out[-1].end_ms <= join_gap_ms:
            out[-1].end_ms = max(out[-1].end_ms, w.end_ms)
        else:
            out.append(Segment(w.start_ms, w.end_ms, str(s)))
    return out


def _activity(segments: Sequence[Segment | SpeakerTurn], labels: list[str], n_frames: int) -> np.ndarray:
    """Boolean [n_speakers, n_frames]: speaker active in frame (frame centre inside a segment)."""
    act = np.zeros((len(labels), n_frames), dtype=bool)
    index = {lab: i for i, lab in enumerate(labels)}
    for seg in segments:
        a = int(np.ceil((seg.start_ms - FRAME_MS / 2) / FRAME_MS))
        b = int(np.ceil((seg.end_ms - FRAME_MS / 2) / FRAME_MS))
        a, b = max(a, 0), min(b, n_frames)
        if b > a:
            act[index[str(seg.speaker)], a:b] = True
    return act


def optimal_mapping(overlap: np.ndarray) -> list[tuple[int, int]]:
    """One to one (ref, hyp) pairs maximising total overlap. Hungarian via scipy when available,
    exhaustive search otherwise (speaker counts per clip are small)."""
    n_ref, n_hyp = overlap.shape
    if n_ref == 0 or n_hyp == 0:
        return []
    try:
        from scipy.optimize import linear_sum_assignment

        rows, cols = linear_sum_assignment(-overlap)
        pairs = list(zip(rows.tolist(), cols.tolist(), strict=True))
    except ImportError:  # pragma: no cover - scipy is present in the dev environment
        best, pairs = -1.0, []
        k = min(n_ref, n_hyp)
        for refs in itertools.combinations(range(n_ref), k):
            for hyps in itertools.permutations(range(n_hyp), k):
                total = float(sum(overlap[r, h] for r, h in zip(refs, hyps, strict=True)))
                if total > best:
                    best, pairs = total, list(zip(refs, hyps, strict=True))
    return [(r, h) for r, h in pairs if overlap[r, h] > 0]


def score_clip(
    turns: Sequence[SpeakerTurn],
    hyp: Sequence[Segment],
    *,
    collar_ms: int = COLLAR_MS,
) -> DiarizationScore:
    ref_labels = sorted({str(t.speaker) for t in turns})
    hyp_labels = sorted({s.speaker for s in hyp})
    end = max([t.end_ms for t in turns] + [s.end_ms for s in hyp] + [0])
    n_frames = int(np.ceil(end / FRAME_MS)) + 1
    ref = _activity(turns, ref_labels, n_frames)
    hy = _activity(hyp, hyp_labels, n_frames)
    scored = np.ones(n_frames, dtype=bool)
    if collar_ms > 0:
        centres = np.arange(n_frames) * FRAME_MS + FRAME_MS / 2
        for t in turns:
            for edge in (t.start_ms, t.end_ms):
                scored &= np.abs(centres - edge) >= collar_ms
    ref, hy = ref[:, scored], hy[:, scored]
    n_ref = ref.sum(axis=0)
    n_hyp = hy.sum(axis=0)
    overlap = (ref[:, None, :] & hy[None, :, :]).sum(axis=2).astype(float) if len(ref_labels) and len(
        hyp_labels
    ) else np.zeros((len(ref_labels), len(hyp_labels)))
    pairs = optimal_mapping(overlap)
    correct = np.zeros(ref.shape[1], dtype=int)
    for r, h in pairs:
        correct += ref[r] & hy[h]
    missed = np.maximum(n_ref - n_hyp, 0).sum()
    fa = np.maximum(n_hyp - n_ref, 0).sum()
    conf = (np.minimum(n_ref, n_hyp) - correct).sum()
    return DiarizationScore(
        scored_ms=float(n_ref.sum() * FRAME_MS),
        missed_ms=float(missed * FRAME_MS),
        false_alarm_ms=float(fa * FRAME_MS),
        confusion_ms=float(conf * FRAME_MS),
        ref_speakers=len(ref_labels),
        hyp_speakers=len(hyp_labels),
        mapping={ref_labels[r]: hyp_labels[h] for r, h in pairs},
    )


# ---------------------------------------------------------------- run level


def score_frame(run_id: str, settings: Settings) -> pd.DataFrame:
    inputs = load_run(run_id, settings)
    if inputs is None:
        return pd.DataFrame(columns=CLIP_COLUMNS)
    main = inputs.main_rows()
    diarizing = {
        p for (p, _v, rep), h in inputs.hyps.items() if rep == 0 and h.speakers and any(h.speakers)
    }
    out: list[dict[str, Any]] = []
    for row in main.itertuples(index=False):
        ref = inputs.ref(row.video_id, row.tier)
        if ref is None or not ref.speaker_turns:
            continue
        hyp = inputs.hyp(row.provider, row.video_id)
        if hyp is None:
            continue
        has_labels = bool(hyp.speakers) and any(hyp.speakers or [])
        empty = not hyp.words and not hyp.text.strip()
        if not has_labels and not (empty and row.provider in diarizing):
            continue
        sc = score_clip(ref.speaker_turns, hypothesis_segments(hyp.words, hyp.speakers))
        out.append(
            {
                "provider": row.provider,
                "model_id": row.model_id,
                "video_id": row.video_id,
                "tier": row.tier,
                "language": row.language,
                "conditions": json.dumps(ref.conditions),
                "ref_speakers": sc.ref_speakers,
                "hyp_speakers": sc.hyp_speakers,
                "speaker_count_correct": sc.speaker_count_correct,
                "scored_ms": sc.scored_ms,
                "missed_ms": sc.missed_ms,
                "false_alarm_ms": sc.false_alarm_ms,
                "confusion_ms": sc.confusion_ms,
                "der": sc.der,
            }
        )
    return pd.DataFrame(out, columns=CLIP_COLUMNS)


def _ratio(num: float, den: float) -> float:
    return float(num / den) if den else float("nan")


def aggregate(clips: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    recs: list[dict[str, Any]] = []
    if clips.empty:
        return pd.DataFrame(columns=AGG_COLUMNS)
    for (tier, language), part in clips.groupby(["tier", "language"], sort=True):
        for kind, name, mask in slices(part):
            for provider, g in part[mask].groupby("provider", sort=True):
                err = (g["missed_ms"] + g["false_alarm_ms"] + g["confusion_ms"]).to_numpy()
                total = g["scored_ms"].to_numpy()
                der = bootstrap_ci(err, total, seed=seed)
                ones = np.ones(len(g))
                count = bootstrap_ci(g["speaker_count_correct"].astype(float).to_numpy(), ones, seed=seed)
                den = float(total.sum())
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
                        "der": der.rate if den else float("nan"),
                        "der_ci_low": der.ci_low if den else float("nan"),
                        "der_ci_high": der.ci_high if den else float("nan"),
                        "missed_rate": _ratio(g["missed_ms"].sum(), den),
                        "false_alarm_rate": _ratio(g["false_alarm_ms"].sum(), den),
                        "confusion_rate": _ratio(g["confusion_ms"].sum(), den),
                        "speaker_count_acc": count.rate,
                        "speaker_count_ci_low": count.ci_low,
                        "speaker_count_ci_high": count.ci_high,
                    }
                )
    return pd.DataFrame(recs, columns=AGG_COLUMNS)


def score_diarization(run_id: str, settings: Settings | None = None) -> Path | None:
    """Write results/<run_id>/diarization.parquet and diarization_aggregates.parquet.
    Returns the clip file, or None when no clip with speaker turns was diarized."""
    s = settings or get_settings()
    clips = score_frame(run_id, s)
    if clips.empty:
        return None
    out = s.results_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    clips.to_parquet(out / "diarization.parquet", index=False)
    aggregate(clips, s.sample_seed).to_parquet(out / "diarization_aggregates.parquet", index=False)
    return out / "diarization.parquet"


def export_section(run_id: str, settings: Settings) -> dict[str, Any] | None:
    """{"diarization": {...}} for the run export, or None when there is no diarization data.
    Numbers only: no text, no speaker labels."""
    out = settings.results_dir / run_id
    agg_path = out / "diarization_aggregates.parquet"
    if not agg_path.exists() and score_diarization(run_id, settings) is None:
        return None
    if not agg_path.exists():
        return None
    aggs = records(pd.read_parquet(agg_path))
    if not aggs:
        return None
    return {
        "diarization": {
            "method": {"collar_ms": COLLAR_MS, "frame_ms": FRAME_MS, "hyp_join_gap_ms": HYP_JOIN_GAP_MS},
            "aggregates": aggs,
        }
    }
