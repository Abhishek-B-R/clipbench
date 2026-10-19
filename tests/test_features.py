from __future__ import annotations

import json

import typer
from typer.testing import CliRunner

from clipbench.providers.base import load_pricing
from clipbench.providers.cli import register
from clipbench.providers.export_v3 import export_section
from clipbench.providers.features import FEATURE_KEYS, feature_matrix, missing_cells
from clipbench.providers.registry import REGISTRY

BATCH_APIS = [
    "groq-whisper", "deepgram", "assemblyai", "elevenlabs-scribe", "gemini-flash", "aws-transcribe",
    "cf-whisper-large-v3-turbo", "cf-whisper", "cf-whisper-tiny-en", "cf-deepgram-nova-3",
    "whisper-local", "whisper-local-turbo",
]


def test_every_batch_provider_has_a_full_features_table():
    pricing = load_pricing()
    for pid in BATCH_APIS:
        table = pricing[pid]["features"]
        for key in FEATURE_KEYS:
            assert f"{key}_url" in table and table[f"{key}_url"].startswith("https://"), (pid, key)
            assert table[f"{key}_checked_on"] == "2026-10-19", (pid, key)
        assert set(table["languages"]) <= {"en", "ja", "ko", "es", "hi"}
        for key in ("batch", "streaming", "word_timestamps", "diarization", "language_id", "custom_vocabulary"):
            assert isinstance(table[key], bool), (pid, key)
    assert missing_cells() == []


def test_matrix_matches_adapter_options():
    """A provider whose adapter implements an option must not claim the feature is missing."""
    from clipbench.providers.registry import provider_class

    for rec in feature_matrix():
        cls = provider_class(rec["provider"])
        f = rec["features"]
        if "diarize" in cls.supported_options:
            assert f["diarization"]["value"] is True, rec["provider"]
        if "detect_language" in cls.supported_options:
            assert f["language_id"]["value"] is True, rec["provider"]
        if "keyterms" in cls.supported_options:
            assert f["custom_vocabulary"]["value"] is True, rec["provider"]


def test_matrix_order_and_export(tmp_settings):
    matrix = feature_matrix()
    ids = [r["provider"] for r in matrix]
    assert ids == [p for p in REGISTRY if p in ids]
    assert "fake" not in ids
    section = export_section("any-run", tmp_settings)
    assert section["features"] == matrix
    json.dumps(section)


def test_cli_prints_matrix():
    app = typer.Typer()
    register(app)
    res = CliRunner().invoke(app, ["providers", "features", "--json"])
    assert res.exit_code == 0, res.output
    assert {r["provider"] for r in json.loads(res.output)} >= set(BATCH_APIS)
    res = CliRunner().invoke(app, ["providers", "features", "--urls"])
    assert res.exit_code == 0 and "deepgram" in res.output
