from __future__ import annotations

import json

import pandas as pd

from clipbench.report import publish

ES_REF = "hoy vamos a aprender diez palabras nuevas para hablar con tus amigos en la calle"
JA_REF = "今日は日本語の勉強をしましょう。新しい言葉をたくさん覚えて、友達と話してみてください。"


def _setup(settings):
    runs = settings.runs_text_dir / "r1"
    runs.mkdir(parents=True)
    pd.DataFrame(
        [
            {"video_id": "es1", "tier": "verified", "ref_text": ES_REF, "words_json": None, "tags": "[]"},
            {"video_id": "ja1", "tier": "verified", "ref_text": JA_REF, "words_json": None, "tags": "[]"},
        ]
    ).to_parquet(runs / "refs.parquet")
    settings.site_dir.mkdir(parents=True)
    settings.results_dir.mkdir(parents=True)
    (settings.site_dir / "index.html").write_text(
        "<html><body><p>clean report, nothing copied</p></body></html>"
    )


def _reasons(settings):
    return [o for o in publish.check(settings) if o.path.name != ".gitignore"]


def test_clean_site_passes(tmp_settings):
    _setup(tmp_settings)
    assert _reasons(tmp_settings) == []


def test_wav_is_caught_by_extension_and_magic(tmp_settings):
    _setup(tmp_settings)
    (tmp_settings.site_dir / "clip.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    (tmp_settings.results_dir / "renamed.bin").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    names = {o.path.name for o in _reasons(tmp_settings)}
    assert names == {"clip.wav", "renamed.bin"}


def test_es_nine_word_span_caught(tmp_settings):
    _setup(tmp_settings)
    span = " ".join(ES_REF.split()[2:11])  # 9 words
    (tmp_settings.site_dir / "index.html").write_text(f"<p>Example: &quot;{span.capitalize()}.&quot;</p>")
    offenders = _reasons(tmp_settings)
    assert [o.path.name for o in offenders] == ["index.html"]


def test_es_eight_word_quote_passes(tmp_settings):
    _setup(tmp_settings)
    span = " ".join(ES_REF.split()[2:10])  # 8 words
    (tmp_settings.results_dir / "report.md").write_text(f"> {span}\n")
    assert _reasons(tmp_settings) == []


def test_ja_span_caught_in_json_with_escapes(tmp_settings):
    _setup(tmp_settings)
    span = "新しい言葉をたくさん覚えて、友達と話して"  # well over 8 words
    (tmp_settings.results_dir / "errors.json").write_text(json.dumps({"x": span}))  # ensure_ascii escapes
    offenders = _reasons(tmp_settings)
    assert [o.path.name for o in offenders] == ["errors.json"]


def test_ja_short_quote_passes(tmp_settings):
    _setup(tmp_settings)
    (tmp_settings.results_dir / "errors.json").write_text(
        json.dumps({"x": "今日は日本語"}, ensure_ascii=False)
    )
    assert _reasons(tmp_settings) == []


def test_parquet_string_columns_scanned(tmp_settings):
    _setup(tmp_settings)
    pd.DataFrame({"t": [ES_REF]}).to_parquet(tmp_settings.results_dir / "leak.parquet")
    assert [o.path.name for o in _reasons(tmp_settings)] == ["leak.parquet"]


def test_verified_json_is_a_reference(tmp_settings):
    _setup(tmp_settings)
    tmp_settings.verified_dir.mkdir(parents=True)
    text = "una frase completamente distinta que solo existe en el archivo verificado de hoy"
    (tmp_settings.verified_dir / "v9.json").write_text(json.dumps({"video_id": "v9", "text": text}))
    (tmp_settings.site_dir / "notes.txt").write_text(text)
    assert [o.path.name for o in _reasons(tmp_settings)] == ["notes.txt"]


def test_repo_gitignore_ok(tmp_settings):
    assert not [o for o in publish.check(tmp_settings) if o.path.name == ".gitignore"]
