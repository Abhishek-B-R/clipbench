"""Five synthetic fixture clips: sine tones as 16 kHz mono WAV plus made-up Japanese references.

No real creator audio or captions. Usable as a module (`make_clips(dir)`) or a script:

    .venv/bin/python tests/fixtures/make_clips.py <out_dir>
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from clipbench.models import Clip, Reference

SAMPLE_RATE = 16_000

# (video id, seconds, tone Hz, reference text, tags). Ids are 11 chars like YouTube's, but invented.
FIXTURES = [
    ("fixture0001", 3.0, 220.0, "今日はいい天気ですね。", []),
    ("fixture0002", 4.5, 330.0, "駅まで歩いて十分くらいです。", ["fast_speech"]),
    ("fixture0003", 2.0, 440.0, "ありがとうございます。", ["background_music"]),
    ("fixture0004", 6.0, 550.0, "週末は友達と映画を見に行きました。", []),
    ("fixture0005", 5.0, 660.0, "このラーメンはとてもおいしいです。", ["noisy", "background_music"]),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_clips(out_dir: Path, tier: str = "verified", language: str = "ja") -> list[Clip]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Clip] = []
    for video_id, seconds, hz, text, tags in FIXTURES:
        path = out_dir / f"{video_id}.wav"
        if not path.exists():
            t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
            tone = (0.2 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
            sf.write(str(path), tone, SAMPLE_RATE, subtype="PCM_16")
        sha = _sha256(path)
        ref = Reference(video_id=video_id, language=language, tier=tier, text=text, tags=list(tags), audio_sha256=sha)
        clips.append(
            Clip(
                video_id=video_id,
                language=language,
                duration_s=seconds,
                channel="fixture-channel",
                audio_path=str(path),
                audio_sha256=sha,
                reference=ref,
            )
        )
    return clips


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "fixture_clips")
    for c in make_clips(target):
        print(c.video_id, c.duration_s, c.audio_path)
