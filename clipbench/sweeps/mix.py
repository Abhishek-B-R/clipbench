"""Mixing speech with a bed at an exact SNR, and synthetic reverb. Pure numpy.

Speech level is measured over **active speech** only, so pauses do not pull the speech level down and
make a mix look cleaner than it sounds:

1. The signal is cut into 20 ms frames (no overlap).
2. A frame is active when its RMS level is at most ACTIVE_DROP_DB (30 dB) below the 95th percentile frame
   level of the clip and above ABS_FLOOR_DBFS (-70 dBFS). Frames inside excluded intervals (the pads
   and any tagged non-speech span) are never active.
3. Speech RMS is the RMS over the samples of the active frames.

Bed level is the RMS over the whole bed segment that is mixed in (every sample, since the bed plays under
the whole clip). Then

    SNR_dB = 20 log10(rms_speech_active / (gain * rms_bed))   so   gain = rms_speech / (rms_bed * 10^(SNR/20))

and mix = speech + gain * bed. If the mix would clip (peak above 0.99), the whole mix is scaled down,
which leaves the SNR unchanged; the scale is recorded in the recipe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 20
ACTIVE_DROP_DB = 30.0
ABS_FLOOR_DBFS = -70.0
PEAK_LIMIT = 0.99
EPS = 1e-12


def _db(x: np.ndarray | float) -> np.ndarray | float:
    return 20 * np.log10(np.maximum(x, EPS))


def rms(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def active_mask(
    speech: np.ndarray,
    sr: int = SAMPLE_RATE,
    exclude: list[tuple[float, float]] | None = None,
    *,
    frame_ms: int = FRAME_MS,
    drop_db: float = ACTIVE_DROP_DB,
    floor_dbfs: float = ABS_FLOOR_DBFS,
) -> np.ndarray:
    """Per sample boolean mask of active speech (see module doc). `exclude` holds (start_s, end_s)."""
    x = np.asarray(speech, dtype=np.float64)
    n = len(x)
    flen = max(1, int(sr * frame_ms / 1000))
    n_frames = n // flen
    mask = np.zeros(n, dtype=bool)
    if n_frames == 0:
        return mask
    frames = x[: n_frames * flen].reshape(n_frames, flen)
    levels = _db(np.sqrt(np.mean(frames * frames, axis=1)))
    allowed = np.ones(n_frames, dtype=bool)
    for start_s, end_s in exclude or []:
        a = max(0, int(np.floor(start_s * sr / flen)))
        b = min(n_frames, int(np.ceil(end_s * sr / flen)))
        allowed[a:b] = False
    if not allowed.any():
        return mask
    top = float(np.percentile(levels[allowed], 95))
    active = allowed & (levels >= top - drop_db) & (levels > floor_dbfs)
    mask[: n_frames * flen] = np.repeat(active, flen)
    return mask


def active_rms(speech: np.ndarray, mask: np.ndarray) -> float:
    return rms(np.asarray(speech)[mask]) if mask.any() else 0.0


def bed_segment(bed: np.ndarray, n: int, offset: int) -> np.ndarray:
    """`n` samples of the bed from `offset`, looping the bed when it is shorter than needed."""
    bed = np.asarray(bed, dtype=np.float32)
    if len(bed) == 0:
        raise ValueError("empty bed")
    reps = int(np.ceil((offset + n) / len(bed))) + 1
    tiled = np.tile(bed, reps) if reps > 1 else bed
    return tiled[offset : offset + n]


@dataclass
class MixInfo:
    snr_db: float
    gain: float
    scale: float
    speech_rms_dbfs: float
    bed_rms_dbfs: float
    active_speech_s: float
    measured_snr_db: float


class SilentSpeech(ValueError):
    """No active speech frames: the SNR is undefined."""


def mix_at_snr(
    speech: np.ndarray,
    bed: np.ndarray,
    snr_db: float,
    mask: np.ndarray,
    sr: int = SAMPLE_RATE,
) -> tuple[np.ndarray, MixInfo]:
    """speech + gain * bed with the active-speech SNR exactly `snr_db`; scaled down if it would clip."""
    s = np.asarray(speech, dtype=np.float64)
    b = np.asarray(bed, dtype=np.float64)
    if len(b) != len(s):
        raise ValueError("speech and bed segment must have the same length")
    rs = active_rms(s, mask)
    if rs <= 0:
        raise SilentSpeech("no active speech")
    rb = rms(b)
    if rb <= 0:
        raise ValueError("silent bed segment")
    gain = rs / (rb * 10 ** (snr_db / 20))
    mixed = s + gain * b
    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    scale = PEAK_LIMIT / peak if peak > PEAK_LIMIT else 1.0
    mixed *= scale
    measured = float(_db(active_rms(s * scale, mask)) - _db(rms(gain * scale * b)))
    info = MixInfo(
        snr_db=float(snr_db),
        gain=float(gain),
        scale=float(scale),
        speech_rms_dbfs=float(_db(rs)),
        bed_rms_dbfs=float(_db(rb)),
        active_speech_s=float(mask.sum() / sr),
        measured_snr_db=measured,
    )
    return mixed.astype(np.float32), info


def synthetic_ir(rt60_s: float, seed: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Room impulse response: a unit direct path, then exponentially decaying Gaussian noise.

    Amplitude envelope exp(-6.908 t / RT60) (60 dB of decay at RT60), length 1.2 x RT60, tail starting
    after 2.5 ms, tail energy equal to the direct path energy (direct to reverberant ratio 0 dB).
    CC0 by construction: generated from the seed, nothing downloaded.
    """
    if rt60_s <= 0:
        raise ValueError("rt60 must be positive")
    n = max(2, int(sr * rt60_s * 1.2))
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    tail = rng.standard_normal(n) * np.exp(-6.908 * t / rt60_s)
    tail[: int(0.0025 * sr)] = 0.0
    energy = float(np.sum(tail * tail))
    if energy > 0:
        tail /= np.sqrt(energy)
    ir = tail
    ir[0] = 1.0
    return ir.astype(np.float64)


def convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """FFT convolution truncated to len(x) (the reverb tail past the end is dropped)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)
    return y[: len(x)]


def apply_reverb(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Reverberant signal with the same active level as the dry one (so SNRs stay comparable)."""
    y = convolve(x, ir)
    dry, wet = rms(x), rms(y)
    return y * (dry / wet) if wet > 0 else y


def to_pcm16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(np.asarray(x, dtype=np.float64) * 32767), -32768, 32767).astype(np.int16)
