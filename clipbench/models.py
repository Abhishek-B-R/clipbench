"""Shared data models. See docs/INTERFACES.md for the on-disk schemas built from these."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Tier = Literal["verified", "creator"]
TrackKind = Literal["creator", "auto", "auto_translation", "unknown"]

TAGS = [
    "background_music",
    "multiple_speakers",
    "fast_speech",
    "code_switching",
    "slang_or_dialect",
    "noisy",
    "caption_not_verbatim",
    "unusable",
]


class Word(BaseModel):
    text: str
    start_ms: int
    end_ms: int


class Track(BaseModel):
    """One caption file on disk, classified from info.json."""

    video_id: str
    language: str
    key: str  # yt-dlp track key, e.g. "ja", "ja-orig", "ja-AN2ECZLziws"
    kind: TrackKind
    path: str


class Reference(BaseModel):
    video_id: str
    language: str
    tier: Tier
    text: str
    words: list[Word] | None = None  # timings, when the source track has them
    tags: list[str] = Field(default_factory=list)
    audio_sha256: str | None = None


class Clip(BaseModel):
    """A clip ready to transcribe: audio on disk plus its reference."""

    video_id: str
    language: str
    duration_s: float
    channel: str | None = None
    audio_path: str  # 16 kHz mono WAV
    audio_sha256: str
    reference: Reference

    @property
    def tier(self) -> Tier:
        return self.reference.tier

    @property
    def tags(self) -> list[str]:
        return self.reference.tags


class Result(BaseModel):
    """What every provider adapter returns."""

    text: str
    words: list[Word] | None = None
    latency_ms: float
    audio_seconds: float
    cost_usd: float
    raw_response: Any = None
    model_id: str
    provider_version: str | None = None
