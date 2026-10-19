from pathlib import Path

import pytest

from clipbench.data.json3 import (
    Json3Error,
    estimate_duration_s,
    parse_json3_lines,
    parse_json3_text,
    parse_json3_words,
)

FIX = Path(__file__).parent / "fixtures" / "json3"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_lines_cut_at_next_start_and_skip_append_events():
    lines = parse_json3_lines(read("asr.json3"), "ja")
    assert [(t.start_ms, t.end_ms, t.text) for t in lines] == [
        (100, 1000, "[音楽]"),
        (1000, 2500, "こんにちは皆さん"),
        (2500, 4500, "今日は晴れです"),
    ]


def test_sound_tags_dropped_only_when_asked():
    lines = parse_json3_lines(read("asr.json3"), "ja", strip_sound_tags=True)
    assert [t.text for t in lines] == ["こんにちは皆さん", "今日は晴れです"]
    assert parse_json3_text(read("asr.json3"), "ja", strip_sound_tags=True) == "こんにちは皆さん今日は晴れです"


def test_creator_text_left_as_written():
    text = parse_json3_text(read("creator.json3"), "ja")
    # newline inside an event is nothing in ja; the bracket stays; blank events are dropped
    assert text == "こんにちは皆さん[笑] 今日は晴れです"


def test_spaced_language_joins_with_spaces():
    assert parse_json3_text(read("creator_es.json3"), "es") == "Hola a todos hoy hace sol"


def test_words_from_word_timed_track():
    words = parse_json3_words(read("asr.json3"))
    assert [(w.text, w.start_ms, w.end_ms) for w in words] == [
        ("こんにちは", 1000, 1600),
        ("皆さん", 1600, 2500),
        ("今日は", 2500, 2900),
        ("晴れ", 2900, 3400),
        ("です", 3400, 4500),
    ]


def test_creator_track_without_offsets_has_no_words():
    assert parse_json3_words(read("creator.json3")) == []


def test_bad_input():
    with pytest.raises(Json3Error):
        parse_json3_lines("not json", "ja")
    with pytest.raises(Json3Error):
        parse_json3_lines('{"no": "events"}', "ja")
    assert parse_json3_words("not json") == []


def test_estimate_duration():
    assert estimate_duration_s(read("asr.json3")) == pytest.approx(6.0)
    assert estimate_duration_s('{"events": []}') is None


def test_unspaced_language_keeps_word_gaps_between_latin_words_across_line_breaks():
    from clipbench.data.json3 import tidy

    assert tidy("今日は\nいい天気", "ja") == "今日はいい天気"
    assert tidy("SP\nsome\nto baby", "ja") == "SP some to baby"
    assert tidy("ママの\nmorning\nルーティーン", "ja") == "ママのmorningルーティーン"
