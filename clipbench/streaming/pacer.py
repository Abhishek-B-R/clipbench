"""Feed a WAV to a provider the way a microphone would: 16 kHz mono PCM in fixed chunks at real-time pace.

Chunk i is sent at `start + i * chunk_ms / speed` (start = when the first chunk went out), so the schedule
never drifts even if a send is slow. After the last chunk the pacer waits until the audio would have finished
playing (`start + duration / speed`) and only then returns, so the end-of-stream message goes out when a live
speaker would have stopped. `speed` > 1 plays faster than real time; it exists for tests and is recorded in
every row, because latency numbers at another speed are not comparable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile

from clipbench.streaming.base import Clock, RealClock

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # signed 16-bit little endian


def read_pcm(path: Path | str, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Mono 16-bit little-endian PCM at `sample_rate`. Stereo is averaged; another rate is resampled linearly
    (the pipeline already writes 16 kHz mono WAVs, so this is only a safety net)."""
    data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != sample_rate and len(mono):
        n_out = int(round(len(mono) * sample_rate / rate))
        x_old = np.arange(len(mono)) / rate
        x_new = np.arange(n_out) / sample_rate
        mono = np.interp(x_new, x_old, mono).astype("float32")
    pcm = np.clip(np.round(mono * 32767.0), -32768, 32767).astype("<i2")
    return pcm.tobytes()


def pcm_seconds(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / (BYTES_PER_SAMPLE * sample_rate)


@dataclass
class PaceLog:
    start: float  # clock time the first chunk went out
    sent_at: list[float]  # clock time of every chunk
    audio_end: float  # clock time the audio finished "playing"


@dataclass
class Pacer:
    chunk_ms: int = 100
    speed: float = 1.0
    sample_rate: int = SAMPLE_RATE
    clock: Clock = field(default_factory=RealClock)

    def __post_init__(self) -> None:
        if self.chunk_ms <= 0:
            raise ValueError("chunk_ms must be positive")
        if not self.speed > 0:
            raise ValueError("speed must be positive")

    @property
    def chunk_bytes(self) -> int:
        return self.sample_rate * BYTES_PER_SAMPLE * self.chunk_ms // 1000

    def chunks(self, pcm: bytes) -> list[bytes]:
        size = self.chunk_bytes
        return [pcm[i : i + size] for i in range(0, len(pcm), size)]

    async def play(self, pcm: bytes, send: Callable[[bytes], Awaitable[None]]) -> PaceLog:
        chunks = self.chunks(pcm)
        step = self.chunk_ms / 1000.0 / self.speed
        start = self.clock.now()
        sent_at: list[float] = []
        for i, chunk in enumerate(chunks):
            delay = start + i * step - self.clock.now()
            if delay > 0:
                await self.clock.sleep(delay)
            sent_at.append(self.clock.now())
            await send(chunk)
        audio_end = start + pcm_seconds(pcm, self.sample_rate) / self.speed
        delay = audio_end - self.clock.now()
        if delay > 0:
            await self.clock.sleep(delay)
        return PaceLog(start=sent_at[0] if sent_at else start, sent_at=sent_at, audio_end=audio_end)
