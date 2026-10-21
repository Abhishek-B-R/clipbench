"""Synthetic world for the end to end pipeline test (tests/test_e2e_pipeline.py).

Everything here is invented: clip ids, channel ids, caption text and audio (sine tones plus seeded
noise). Nothing is downloaded and nothing comes from a real creator. The files are laid out exactly
where the real importers and fetchers would put them:

- datasets/<lang>/clips.csv                   (the public dataset spec)
- data/info/<id>.info.json                    (shaped like a slimmed yt-dlp info.json)
- data/tracks/<id>.<key>.json3                (YouTube json3 caption tracks, word timed)
- data/audio/<clip_id>.wav + hashes.json      (what `clipbench data fetch-audio` leaves behind)
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16_000

# Invented Spanish-looking vocabulary. Random draws from it never form a real sentence, so no 9 word
# window of a reference can turn up by chance in report prose or labels.
VOCAB = (
    "brisa canela farol jilguero lunar marea nube olivo pradera quinta romero salvia tilo umbral vereda "
    "zarza alondra bruma cauce duna espiga fresno granizo helecho islote jara lirio musgo nogal ocaso "
    "pino rocio sauce trigo valle yedra ambar cobre dalia estero fragua garza hiedra junco loma manantial "
    "nectar orilla palmera quebrada ribera sendero tomillo urraca viento zorzal acequia baya"
).split()

CATEGORIES6 = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sentence(seed: int, n_words: int = 14) -> str:
    rng = random.Random(seed)
    return " ".join(rng.choice(VOCAB) for _ in range(n_words))


def json3_track(text: str, offset_ms: int = 300, word_ms: int = 380) -> str:
    """A word timed json3 track: one event per four words, each word a seg with tOffsetMs."""
    words = text.split()
    events = [{"tStartMs": 0, "dDurationMs": 60_000, "id": 1, "wpWinPosId": 1, "wsWinStyleId": 1}]
    t = offset_ms
    for i in range(0, len(words), 4):
        chunk = words[i : i + 4]
        segs = [{"utf8": chunk[0]}] + [
            {"utf8": " " + w, "tOffsetMs": (j + 1) * word_ms} for j, w in enumerate(chunk[1:])
        ]
        events.append({"tStartMs": t, "dDurationMs": word_ms * len(chunk), "wWinId": 1, "segs": segs})
        t += word_ms * len(chunk)
    return json.dumps({"wireMagic": "pb3", "pens": [{}], "events": events}, ensure_ascii=False)


def info_json(
    video_id: str,
    *,
    duration: float,
    channel_id: str,
    title: str,
    language: str,
    subtitles: list[str],
    automatic: list[str],
) -> dict:
    """The keys clipbench reads from a yt-dlp info.json (clipbench.data.info.slim_info keeps these)."""
    fmt = [{"ext": "json3", "url": "https://example.invalid/timedtext"}]
    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "language": language,
        "channel": f"Synthetic {channel_id}",
        "channel_id": channel_id,
        "uploader_id": channel_id,
        "categories": ["People & Blogs"],
        "tags": [],
        "subtitles": {k: fmt for k in subtitles},
        "automatic_captions": {k: fmt for k in automatic},
    }


@dataclass
class SynthClip:
    """One clips.csv row plus what its caption store and info.json look like."""

    video_id: str
    channel_id: str
    duration: float
    category: str = ""  # clips.csv category; blank means the manifest guesses from the title
    title: str = "synthetic clip"
    start_ms: int | None = None
    end_ms: int | None = None
    creator: bool = True  # a creator-uploaded track listed in info `subtitles`
    translated_only: bool = False  # the `<lang>` track is a YouTube auto translation, not creator
    seed: int = 0

    @property
    def clip_id(self) -> str:
        if self.start_ms is None:
            return self.video_id
        return f"{self.video_id}@{self.start_ms}-{self.end_ms}"


def write_world(clips: list[SynthClip], *, datasets_dir: Path, data_dir: Path, language: str = "es") -> None:
    """clips.csv, info.json files and json3 tracks for `clips`."""
    lang_dir = datasets_dir / language
    lang_dir.mkdir(parents=True, exist_ok=True)
    with (lang_dir / "clips.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["platform", "video_id", "start_ms", "end_ms", "channel_id", "category", "license_note", "source"])
        for c in clips:
            w.writerow(
                [
                    "youtube",
                    c.video_id,
                    "" if c.start_ms is None else c.start_ms,
                    "" if c.end_ms is None else c.end_ms,
                    c.channel_id,
                    c.category,
                    "synthetic test clip",
                    "e2e",
                ]
            )
    info_dir, tracks_dir = data_dir / "info", data_dir / "tracks"
    info_dir.mkdir(parents=True, exist_ok=True)
    tracks_dir.mkdir(parents=True, exist_ok=True)
    for c in clips:
        if c.translated_only:
            subs, auto, spoken = [], ["en-orig", language], "en"
        elif c.creator:
            subs, auto, spoken = [language], [f"{language}-orig"], language
        else:
            subs, auto, spoken = [], [f"{language}-orig"], language
        info = info_json(
            c.video_id,
            duration=c.duration,
            channel_id=c.channel_id,
            title=c.title,
            language=spoken,
            subtitles=subs,
            automatic=auto,
        )
        (info_dir / f"{c.video_id}.info.json").write_text(json.dumps(info), encoding="utf-8")
        text = creator_text(c)
        if c.start_ms is not None:
            # A long video: the caption runs from 0, the window's words start at start_ms.
            lead = sentence(c.seed + 9_000, 6)
            track = json3_track(lead + " " + text, offset_ms=c.start_ms - 6 * 380 + 100)
        else:
            track = json3_track(text)
        if c.creator or c.translated_only:
            (tracks_dir / f"{c.video_id}.{language}.json3").write_text(track, encoding="utf-8")
        (tracks_dir / f"{c.video_id}.{language}-orig.json3").write_text(json3_track(sentence(c.seed + 5_000)), encoding="utf-8")


def creator_text(c: SynthClip) -> str:
    return sentence(c.seed)


def write_wav(path: Path, seconds: float, seed: int) -> str:
    """A 16 kHz mono PCM WAV of a tone plus seeded noise. Returns its sha256."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    hz = 180.0 + (seed % 37) * 11.0
    sig = 0.15 * np.sin(2 * np.pi * hz * t) + 0.02 * rng.standard_normal(t.size)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), sig.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")
    return sha256(path)


def place_audio(audio_dir: Path, clips: dict[str, float], seed: int = 0) -> dict[str, dict[str, str]]:
    """WAVs and hashes.json where `clipbench data fetch-audio` leaves them. `clips`: clip_id -> seconds.
    No original download exists, so the `original` hash is the WAV's own (fetch-audio records both)."""
    hashes: dict[str, dict[str, str]] = {}
    for i, (clip_id, seconds) in enumerate(sorted(clips.items())):
        sha = write_wav(audio_dir / f"{clip_id}.wav", seconds, seed + i)
        hashes[clip_id] = {"original": sha, "wav": sha}
    (audio_dir / "hashes.json").write_text(json.dumps(hashes, indent=1), encoding="utf-8")
    return hashes
