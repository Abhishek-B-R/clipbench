"""Provider ids to adapter classes. Imports are lazy so a missing optional package only skips that provider."""

from __future__ import annotations

import importlib

from clipbench.providers.base import Provider

# id -> "module:Class"
REGISTRY: dict[str, str] = {
    "whisper-local": "clipbench.providers.whisper_local:WhisperLocal",
    "whisper-local-turbo": "clipbench.providers.whisper_local:WhisperLocalTurbo",
    "groq-whisper": "clipbench.providers.groq:GroqWhisper",
    "deepgram": "clipbench.providers.deepgram:Deepgram",
    "assemblyai": "clipbench.providers.assemblyai:AssemblyAI",
    "elevenlabs-scribe": "clipbench.providers.elevenlabs:ElevenLabsScribe",
    "gemini-flash": "clipbench.providers.gemini:GeminiFlash",
    "aws-transcribe": "clipbench.providers.aws_transcribe:AwsTranscribe",
    "youtube-auto": "clipbench.providers.youtube_auto:YouTubeAuto",
    "wayin": "clipbench.providers.wayin:Wayin",
    "fake": "clipbench.providers.fake:FakeProvider",
}

# What `--providers all` means: every real system, never the fake one.
ALL = [k for k in REGISTRY if k != "fake"]


def provider_class(provider_id: str) -> type[Provider]:
    if provider_id not in REGISTRY:
        raise KeyError(f"unknown provider {provider_id!r}; known: {', '.join(REGISTRY)}")
    module, name = REGISTRY[provider_id].split(":")
    return getattr(importlib.import_module(module), name)


def resolve(spec: str) -> list[str]:
    """'all' or a comma separated list."""
    if spec.strip() == "all":
        return list(ALL)
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    for i in ids:
        if i not in REGISTRY:
            raise KeyError(f"unknown provider {i!r}; known: {', '.join(REGISTRY)}")
    return ids


def make(provider_id: str, **options) -> Provider:
    return provider_class(provider_id)(**options)
