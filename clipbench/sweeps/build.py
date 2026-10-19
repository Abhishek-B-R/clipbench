"""`clipbench sweep build`: mix clean verified clips with beds at fixed SNRs.

Outputs:
- data/sweeps/<sweep_id>/audio/*.wav          mixed 16 kHz mono WAVs (never published)
- data/sweeps/<sweep_id>/clips.json           the variant clips with their shifted references (private)
- results/sweeps/<sweep_id>/recipes.parquet   one row per variant: how to rebuild it (numbers and ids only)
- results/sweeps/<sweep_id>/sweep_meta.json   settings of the sweep and the beds used (ids and licenses)

Every clip gets PAD_S seconds of bed-only audio before and after the speech (silence for the clean
variant). The pads are known non-speech, so hallucination over non-speech is measured on every clip,
also when the reference has no word timings. For one clip and bed kind, the same bed and offset are used
at every SNR, so the only thing that changes along a sweep is the level.

Variant ids (the `video_id` of the sweep run): `<clip_id>#clean`, `<clip_id>#<kind>_snr<snr>`, plus `_rev`
for the reverb series, e.g. `abc123#music_snr-5_rev`.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clipbench.config import Settings, get_settings
from clipbench.models import Clip, NonSpeechSpan, Word
from clipbench.sweeps import beds as beds_mod
from clipbench.sweeps import mix

PAD_S = 2.0
DEFAULT_SNRS = (20.0, 10.0, 5.0, 0.0, -5.0)
DEFAULT_RT60_S = 0.5
EXCLUDED_TAGS = {"background_music", "noisy"}
EXCLUDED_CONDITIONS = {"music", "outdoor_noise"}

RECIPE_COLUMNS = [
    "sweep_id", "variant_id", "clip_id", "language", "condition", "bed_kind", "bed_id", "bed_license",
    "bed_offset_s", "snr_db", "reverb", "rt60_s", "ir_seed", "seed", "pad_s", "duration_s", "gain", "scale",
    "speech_rms_dbfs", "bed_rms_dbfs", "active_speech_s", "measured_snr_db", "src_sha256", "sha256", "wav_name",
]  # fmt: skip


def sweep_results_dir(sweep_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.results_dir / "sweeps" / sweep_id


def sweep_data_dir(sweep_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.data_dir / "sweeps" / sweep_id


def new_sweep_id(language: str) -> str:
    return f"{language}-{datetime.now(UTC).strftime('%Y%m%d')}-{secrets.token_hex(2)}"


def condition_label(kind: str | None, snr_db: float | None, reverb: bool) -> str:
    base = "clean" if kind is None else f"{kind}_snr{snr_db:g}"
    return base + ("_rev" if reverb else "")


def is_clean(clip: Clip) -> bool:
    """Verified clips without music or noise: no background_music or noisy tag, no music or outdoor_noise
    condition, no non-speech span of kind music."""
    ref = clip.reference
    if ref.tier != "verified":
        return False
    if EXCLUDED_TAGS & set(ref.tags):
        return False
    if EXCLUDED_CONDITIONS & set(getattr(ref, "conditions", []) or []):
        return False
    return not any(s.kind == "music" for s in ref.nonspeech_spans)


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_clip_audio(clip: Clip) -> np.ndarray:
    import soundfile as sf

    audio, sr = sf.read(clip.audio_path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != mix.SAMPLE_RATE:
        raise ValueError(f"{clip.video_id}: {sr} Hz audio, expected {mix.SAMPLE_RATE}")
    return audio.astype(np.float64)


def _variant_clip(
    clip: Clip, variant_id: str, wav: Path, sha: str, pad_s: float, total_s: float, pad_kind: str
) -> Clip:
    ref = clip.reference.model_copy(deep=True)
    pad_ms = int(round(pad_s * 1000))
    total_ms = int(round(total_s * 1000))
    speech_end_ms = pad_ms + int(round(clip.duration_s * 1000))
    ref.video_id = variant_id
    ref.audio_sha256 = sha
    if ref.words:
        ref.words = [Word(text=w.text, start_ms=w.start_ms + pad_ms, end_ms=w.end_ms + pad_ms) for w in ref.words]
    spans = [NonSpeechSpan(start_ms=0, end_ms=pad_ms, kind=pad_kind)] if pad_ms > 0 else []
    spans += [
        NonSpeechSpan(start_ms=s.start_ms + pad_ms, end_ms=s.end_ms + pad_ms, kind=s.kind) for s in ref.nonspeech_spans
    ]
    if total_ms > speech_end_ms:
        spans.append(NonSpeechSpan(start_ms=speech_end_ms, end_ms=total_ms, kind=pad_kind))
    ref.nonspeech_spans = spans
    return Clip(
        video_id=variant_id,
        language=clip.language,
        duration_s=float(total_s),
        channel=clip.channel,
        audio_path=str(wav),
        audio_sha256=sha,
        reference=ref,
    )


def _write_wav(path: Path, audio: np.ndarray) -> str:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.wav")
    sf.write(str(tmp), mix.to_pcm16(audio), mix.SAMPLE_RATE, subtype="PCM_16")
    os.replace(tmp, path)
    return _sha256(path)


@dataclass
class BuildResult:
    sweep_id: str
    recipes: pd.DataFrame
    recipes_path: Path
    meta_path: Path
    clips_path: Path
    n_source_clips: int
    skipped: dict[str, str]


def _pick_beds(
    kinds: list[str], bed_ids: list[str] | None, beds: list[beds_mod.Bed], settings: Settings
) -> dict[str, list[beds_mod.Bed]]:
    pool: dict[str, list[beds_mod.Bed]] = {}
    for kind in kinds:
        chosen = [
            b
            for b in beds_mod.usable_beds(beds, (kind,))
            if (not bed_ids or b.id in bed_ids) and beds_mod.bed_available(b, settings)
        ]
        if not chosen:
            raise FileNotFoundError(
                f"no usable {kind} bed on disk; run `clipbench sweep fetch-beds` (or pick --bed-ids)"
            )
        pool[kind] = sorted(chosen, key=lambda b: b.id)
    return pool


@dataclass
class _Ctx:
    sweep_id: str
    clip: Clip
    mask: np.ndarray
    ir_seed: int
    rt60_s: float
    seed: int
    pad_s: float
    total_s: float
    audio_dir: Path
    variants: list[Clip]
    recipes: list[dict[str, Any]]


def _emit(
    ctx: _Ctx,
    audio: np.ndarray,
    kind: str | None,
    snr: float | None,
    rev: bool,
    bed: beds_mod.Bed | None = None,
    offset: int = 0,
    info: mix.MixInfo | None = None,
) -> None:
    sr = mix.SAMPLE_RATE
    clip = ctx.clip
    cond = condition_label(kind, snr, rev)
    variant_id = f"{clip.video_id}#{cond}"
    wav_name = variant_id.replace("#", "__") + ".wav"
    wav = ctx.audio_dir / wav_name
    sha = _write_wav(wav, audio)
    pad_kind = "silence" if kind is None else ("music" if kind == "music" else "sfx")
    ctx.variants.append(_variant_clip(clip, variant_id, wav, sha, ctx.pad_s, ctx.total_s, pad_kind))
    speech_dbfs = info.speech_rms_dbfs if info else float(mix._db(mix.active_rms(audio, ctx.mask)))
    ctx.recipes.append(
        {
            "sweep_id": ctx.sweep_id,
            "variant_id": variant_id,
            "clip_id": clip.video_id,
            "language": clip.language,
            "condition": cond,
            "bed_kind": kind,
            "bed_id": bed.id if bed else None,
            "bed_license": bed.license if bed else None,
            "bed_offset_s": offset / sr if bed else None,
            "snr_db": snr,
            "reverb": rev,
            "rt60_s": ctx.rt60_s if rev else None,
            "ir_seed": ctx.ir_seed if rev else None,
            "seed": ctx.seed,
            "pad_s": ctx.pad_s,
            "duration_s": ctx.total_s,
            "gain": info.gain if info else None,
            "scale": info.scale if info else 1.0,
            "speech_rms_dbfs": speech_dbfs,
            "bed_rms_dbfs": info.bed_rms_dbfs if info else None,
            "active_speech_s": float(ctx.mask.sum() / sr),
            "measured_snr_db": info.measured_snr_db if info else None,
            "src_sha256": clip.audio_sha256,
            "sha256": sha,
            "wav_name": wav_name,
        }
    )


def build_sweep(
    language: str,
    snrs: list[float] | tuple[float, ...] = DEFAULT_SNRS,
    *,
    reverb: bool = False,
    rt60_s: float = DEFAULT_RT60_S,
    kinds: list[str] | tuple[str, ...] = ("music", "noise"),
    bed_ids: list[str] | None = None,
    limit: int | None = None,
    seed: int = 7,
    pad_s: float = PAD_S,
    sweep_id: str | None = None,
    settings: Settings | None = None,
    clips: list[Clip] | None = None,
    beds: list[beds_mod.Bed] | None = None,
    log=print,
) -> BuildResult:
    settings = settings or get_settings()
    kinds = [k for k in kinds if k]
    for k in kinds:
        if k not in beds_mod.BED_KINDS:
            raise ValueError(f"unknown bed kind {k!r}; use {', '.join(beds_mod.BED_KINDS)}")
    if clips is None:
        from clipbench.data.references import load_clips  # lazy: data module

        clips = load_clips("verified", settings=settings, language=language)
    lang = language.split("-")[0].lower()
    clean = sorted(
        (c for c in clips if c.language.split("-")[0].lower() == lang and is_clean(c)), key=lambda c: c.video_id
    )
    if limit is not None:
        clean = clean[:limit]
    if not clean:
        raise ValueError(f"no clean verified clips for {language} (verified, no music or noise tags or spans)")
    beds = beds if beds is not None else beds_mod.load_manifest(settings=settings)
    pool = _pick_beds(kinds, bed_ids, beds, settings)
    loaded: dict[str, np.ndarray] = {}

    sweep_id = sweep_id or new_sweep_id(lang)
    out_dir = sweep_results_dir(sweep_id, settings)
    data_dir = sweep_data_dir(sweep_id, settings)
    audio_dir = data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    order = {kind: np.random.default_rng(_seed(seed, "beds", kind)).permutation(len(pool[kind])) for kind in kinds}
    recipes: list[dict[str, Any]] = []
    variants: list[Clip] = []
    skipped: dict[str, str] = {}
    sr = mix.SAMPLE_RATE
    pad = int(round(pad_s * sr))
    reverb_states = [False, True] if reverb else [False]

    for i, clip in enumerate(clean):
        try:
            speech = _read_clip_audio(clip)
        except Exception as exc:
            skipped[clip.video_id] = f"unreadable audio: {exc}"
            continue
        padded = np.concatenate([np.zeros(pad), speech, np.zeros(pad)])
        n = len(padded)
        total_s = n / sr
        exclude = [(0.0, pad_s), (pad_s + len(speech) / sr, total_s)]
        exclude += [(pad_s + s.start_ms / 1000, pad_s + s.end_ms / 1000) for s in clip.reference.nonspeech_spans]
        mask = mix.active_mask(padded, sr, exclude)
        if not mask.any():
            skipped[clip.video_id] = "no active speech frames"
            continue
        ir_seed = _seed(seed, clip.video_id, "ir")
        ir = mix.synthetic_ir(rt60_s, ir_seed, sr) if reverb else None
        ctx = _Ctx(sweep_id, clip, mask, ir_seed, rt60_s, seed, pad_s, total_s, audio_dir, variants, recipes)
        for rev in reverb_states:
            voice = mix.apply_reverb(padded, ir) if rev else padded
            _emit(ctx, voice, None, None, rev)
            for kind in kinds:
                bed = pool[kind][order[kind][i % len(pool[kind])]]
                if bed.id not in loaded:
                    loaded[bed.id] = beds_mod.load_bed(bed, settings)
                bed_audio = loaded[bed.id]
                rng = np.random.default_rng(_seed(seed, clip.video_id, kind, "offset"))
                offset = int(rng.integers(0, max(1, len(bed_audio) - n + 1)))
                segment = mix.bed_segment(bed_audio, n, offset)
                for snr in snrs:
                    mixed, info = mix.mix_at_snr(voice, segment, float(snr), mask, sr)
                    _emit(ctx, mixed, kind, float(snr), rev, bed, offset, info)
        log(f"[{i + 1}/{len(clean)}] {clip.video_id}: {len(reverb_states) * (1 + len(kinds) * len(snrs))} variants")

    df = pd.DataFrame(recipes, columns=RECIPE_COLUMNS)
    df["reverb"] = df["reverb"].astype(bool)
    for col in ("bed_offset_s", "snr_db", "rt60_s", "pad_s", "duration_s", "gain", "scale", "speech_rms_dbfs",
                "bed_rms_dbfs", "active_speech_s", "measured_snr_db"):  # fmt: skip
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("ir_seed", "seed"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    recipes_path = out_dir / "recipes.parquet"
    df.to_parquet(recipes_path, index=False)

    clips_path = data_dir / "clips.json"
    clips_path.write_text(json.dumps([c.model_dump() for c in variants], ensure_ascii=False))

    used = sorted({b for b in df["bed_id"].dropna()})
    bed_info = [b.public() for b in beds if b.id in used]
    from clipbench import __version__

    meta = {
        "sweep_id": sweep_id,
        "language": lang,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snrs_db": [float(s) for s in snrs],
        "bed_kinds": list(kinds),
        "beds": bed_info,
        "reverb": reverb,
        "rt60_s": rt60_s if reverb else None,
        "seed": seed,
        "pad_s": pad_s,
        "n_clips": int(df["clip_id"].nunique()) if len(df) else 0,
        "n_variants": int(len(df)),
        "skipped_clips": skipped,
        "active_speech": {"frame_ms": mix.FRAME_MS, "drop_db": mix.ACTIVE_DROP_DB, "floor_dbfs": mix.ABS_FLOOR_DBFS},
        "clipbench_version": __version__,
    }
    meta_path = out_dir / "sweep_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return BuildResult(sweep_id, df, recipes_path, meta_path, clips_path, len(clean), skipped)


def load_variant_clips(sweep_id: str, settings: Settings | None = None) -> list[Clip]:
    path = sweep_data_dir(sweep_id, settings) / "clips.json"
    if not path.is_file():
        raise FileNotFoundError(f"no sweep {sweep_id} ({path}); run `clipbench sweep build` first")
    return [Clip.model_validate(c) for c in json.loads(path.read_text(encoding="utf-8"))]


def load_meta(sweep_id: str, settings: Settings | None = None) -> dict[str, Any]:
    path = sweep_results_dir(sweep_id, settings) / "sweep_meta.json"
    return json.loads(path.read_text()) if path.is_file() else {}
