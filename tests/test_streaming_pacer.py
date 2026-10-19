"""Pacer timing on a fake clock, and PCM reading."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile

from clipbench.streaming.base import FakeClock
from clipbench.streaming.pacer import Pacer, pcm_seconds, read_pcm


def _pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(16000 * seconds)


async def _play(pacer: Pacer, pcm: bytes, send_cost: float = 0.0):
    sizes: list[int] = []

    async def send(chunk: bytes) -> None:
        sizes.append(len(chunk))
        if send_cost:
            pacer.clock.t += send_cost  # a slow send moves the clock

    log = await pacer.play(pcm, send)
    return log, sizes


async def test_real_time_schedule():
    clock = FakeClock()
    log, sizes = await _play(Pacer(chunk_ms=100, clock=clock), _pcm(1.0))
    assert sizes == [3200] * 10  # 100 ms of 16 kHz 16-bit mono
    assert log.sent_at == pytest.approx([i * 0.1 for i in range(10)])
    assert log.start == 0.0
    assert log.audio_end == pytest.approx(1.0)
    assert clock.now() == pytest.approx(1.0)  # waits until the audio would have finished playing


async def test_speed_factor_and_chunk_size():
    clock = FakeClock()
    log, sizes = await _play(Pacer(chunk_ms=250, speed=2.0, clock=clock), _pcm(1.0))
    assert sizes == [8000] * 4
    assert log.sent_at == pytest.approx([0.0, 0.125, 0.25, 0.375])
    assert log.audio_end == pytest.approx(0.5)


async def test_short_last_chunk():
    clock = FakeClock()
    log, sizes = await _play(Pacer(chunk_ms=100, clock=clock), _pcm(0.25))
    assert sizes == [3200, 3200, 1600]
    assert log.sent_at == pytest.approx([0.0, 0.1, 0.2])
    assert log.audio_end == pytest.approx(0.25)


async def test_schedule_does_not_drift_with_slow_sends():
    clock = FakeClock()
    log, _ = await _play(Pacer(chunk_ms=100, clock=clock), _pcm(0.3), send_cost=0.03)
    assert log.sent_at == pytest.approx([0.0, 0.1, 0.2])


async def test_sends_back_to_back_when_behind():
    clock = FakeClock()
    log, _ = await _play(Pacer(chunk_ms=100, clock=clock), _pcm(0.3), send_cost=0.15)
    assert log.sent_at == pytest.approx([0.0, 0.15, 0.3])
    assert log.audio_end == pytest.approx(0.3)


def test_invalid_pacer():
    with pytest.raises(ValueError):
        Pacer(chunk_ms=0)
    with pytest.raises(ValueError):
        Pacer(speed=0)


def test_read_pcm(tmp_path):
    p = tmp_path / "a.wav"
    soundfile.write(str(p), np.full(16000, 0.5, dtype="float32"), 16000)
    pcm = read_pcm(p)
    assert len(pcm) == 32000 and pcm_seconds(pcm) == pytest.approx(1.0)
    samples = np.frombuffer(pcm, dtype="<i2")
    assert samples[0] == round(0.5 * 32767)

    stereo = tmp_path / "s.wav"
    soundfile.write(str(stereo), np.stack([np.full(8000, 0.5), np.zeros(8000)], axis=1).astype("float32"), 8000)
    pcm = read_pcm(stereo)
    assert len(pcm) == 32000  # resampled to 16 kHz
    assert np.frombuffer(pcm, dtype="<i2")[100] == round(0.25 * 32767)  # channels averaged
