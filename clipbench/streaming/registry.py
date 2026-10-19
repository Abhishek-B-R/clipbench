"""Streaming provider ids to adapter classes. Ids end in `-stream` and are priced in `[<id>]` in pricing.toml."""

from __future__ import annotations

import importlib

from clipbench.streaming.base import StreamingProvider

STREAM_REGISTRY: dict[str, str] = {
    "deepgram-stream": "clipbench.streaming.deepgram:DeepgramStream",
    "assemblyai-stream": "clipbench.streaming.assemblyai:AssemblyAIStream",
    "elevenlabs-stream": "clipbench.streaming.elevenlabs:ElevenLabsStream",
    "gemini-stream": "clipbench.streaming.gemini:GeminiStream",
    "cf-nova-3-stream": "clipbench.streaming.cloudflare:CfNova3Stream",
    "fake-stream": "clipbench.streaming.fake:FakeStream",
}

ALL = [k for k in STREAM_REGISTRY if k != "fake-stream"]


def provider_class(provider_id: str) -> type[StreamingProvider]:
    if provider_id not in STREAM_REGISTRY:
        raise KeyError(f"unknown streaming provider {provider_id!r}; known: {', '.join(STREAM_REGISTRY)}")
    module, name = STREAM_REGISTRY[provider_id].split(":")
    return getattr(importlib.import_module(module), name)


def resolve(spec: str) -> list[str]:
    """'all' or a comma separated list of streaming ids."""
    if spec.strip() == "all":
        return list(ALL)
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    for i in ids:
        if i not in STREAM_REGISTRY:
            raise KeyError(f"unknown streaming provider {i!r}; known: {', '.join(STREAM_REGISTRY)}")
    return ids


def make(provider_id: str, **options) -> StreamingProvider:
    return provider_class(provider_id)(**options)
