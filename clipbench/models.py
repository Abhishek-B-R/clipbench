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


CATEGORIES = [
    "talking_head",
    "vlog_music",
    "gaming",
    "comedy_skit",
    "cooking_tutorial",
    "news_explainer",
    "other",
]


NONSPEECH_KINDS = ["music", "sfx", "silence"]


class NonSpeechSpan(BaseModel):
    start_ms: int
    end_ms: int
    kind: Literal["music", "sfx", "silence"] = "music"


class CodeSwitchSpan(BaseModel):
    start: int  # character offsets into the reference text
    end: int
    lang: str = "en"


CONDITIONS = [
    "music",
    "sfx",
    "multiple_speakers",
    "fast_speech",
    "whisper_shout",
    "outdoor_noise",
    "code_switching",
]

ENTITY_TYPES = ["person", "brand", "place", "product", "other"]
ITN_KINDS = ["number", "date", "time", "currency", "percent", "unit"]


class SpeakerTurn(BaseModel):
    start_ms: int
    end_ms: int
    speaker: str


class EntitySpan(BaseModel):
    start: int  # code point offsets into the reference text
    end: int
    type: Literal["person", "brand", "place", "product", "other"] = "other"


class AlternativeSpan(BaseModel):
    start: int
    end: int
    options: list[str]  # valid spellings; the reference text at [start:end] is always valid too


class ItnSpan(BaseModel):
    start: int
    end: int
    kind: Literal["number", "date", "time", "currency", "percent", "unit"] = "number"


class Reference(BaseModel):
    video_id: str
    language: str
    tier: Tier
    text: str
    words: list[Word] | None = None  # timings, when the source track has them
    tags: list[str] = Field(default_factory=list)
    audio_sha256: str | None = None
    # v2 fields, see docs/V2.md. video_id above is the clip_id.
    platform: str = "youtube"
    source_id: str | None = None  # the platform's own video id
    clip_source: str = "short"  # short | window | long
    start_ms: int | None = None  # window start inside the source video
    category: str | None = None
    nonspeech_spans: list[NonSpeechSpan] = Field(default_factory=list)
    codeswitch_spans: list[CodeSwitchSpan] = Field(default_factory=list)
    punctuated: bool = False
    timing_source: str | None = None  # "creator" | "aligner:<name>" | None
    # v3 fields, see docs/V3.md
    conditions: list[str] = Field(default_factory=list)
    speaker_turns: list[SpeakerTurn] = Field(default_factory=list)
    entity_spans: list[EntitySpan] = Field(default_factory=list)
    alternatives: list[AlternativeSpan] = Field(default_factory=list)
    itn_spans: list[ItnSpan] = Field(default_factory=list)


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
    # v3, see docs/V3.md
    speakers: list[str | None] | None = None  # per word speaker label, parallel to words
    detected_language: str | None = None
    retries: int = 0
    http_status: int | None = None
