from __future__ import annotations

import pytest

from clipbench.scoring import normalize as n

# ---------------------------------------------------------------- shared rules


def test_nfkc_full_width_to_ascii():
    assert n.nfkc("ＡＢＣ１２３") == "ABC123"
    assert n.nfkc("ｶﾀｶﾅ") == "カタカナ"


def test_lowercase():
    assert n.lowercase("HoLa") == "hola"


def test_strip_bracket_tags_square_and_round():
    assert n.strip_bracket_tags("a [Música] b (risas) c").split() == ["a", "b", "c"]
    assert n.strip_bracket_tags("x [a [b] c] y").split() == ["x", "y"]


def test_strip_punctuation_replaces_with_space():
    assert n.strip_punctuation("hola,amigo!") == "hola amigo "
    assert n.strip_punctuation("a♪b", replacement="") == "ab"


def test_strip_punctuation_keeps_long_vowel_and_iteration_marks():
    assert n.strip_punctuation("ラーメン、人々。", replacement="") == "ラーメン人々"


def test_collapse_and_remove_whitespace():
    assert n.collapse_whitespace("  a \t b\n c ") == "a b c"
    assert n.remove_whitespace(" a b　c ") == "abc"


def test_strip_accents_including_enye():
    assert n.strip_accents("canción pingüino año") == "cancion pinguino ano"


# ---------------------------------------------------------------- Spanish


@pytest.mark.parametrize(
    "value,words",
    [
        (0, "cero"),
        (3, "tres"),
        (16, "dieciséis"),
        (21, "veintiuno"),
        (25, "veinticinco"),
        (31, "treinta y uno"),
        (100, "cien"),
        (101, "ciento uno"),
        (555, "quinientos cincuenta y cinco"),
        (1000, "mil"),
        (2001, "dos mil uno"),
        (21000, "veintiún mil"),
        (31000, "treinta y un mil"),
        (999999, "novecientos noventa y nueve mil novecientos noventa y nueve"),
        (1000000, "un millón"),
        (2500000, "dos millones quinientos mil"),
    ],
)
def test_spanish_number_words(value, words):
    assert n.spanish_number_words(value) == words


def test_spanish_numbers_in_text():
    assert n.collapse_whitespace(n.spanish_numbers_to_words("tengo 3 gatos")) == "tengo tres gatos"
    assert n.collapse_whitespace(n.spanish_numbers_to_words("1.500 euros")) == "mil quinientos euros"
    assert n.collapse_whitespace(n.spanish_numbers_to_words("3,5 kilos")) == "tres coma cinco kilos"
    assert n.collapse_whitespace(n.spanish_numbers_to_words("el 25%")) == "el veinticinco por ciento"


def test_normalize_es_full():
    text = "¡Hola! ¿Tienes 3 gatos? [Música] (risas) Año"
    assert n.normalize_es(text) == "hola tienes tres gatos año"


def test_normalize_es_no_accents():
    assert n.normalize_es_no_accents("¿Qué tal, Niño?") == "que tal nino"


# ---------------------------------------------------------------- Japanese


def test_normalize_ja_strips_punctuation_space_and_tags():
    text = "「こんにちは」、世界！ [音楽] (笑) ラーメン　ＡＢＣ１２３。"
    assert n.normalize_ja(text) == "こんにちは世界ラーメンabc123"


def test_normalize_ja_keeps_digits():
    assert n.normalize_ja("２０２４年") == "2024年"


@pytest.mark.parametrize(
    "value,reading",
    [
        (0, "ぜろ"),
        (10, "じゅう"),
        (24, "にじゅうよん"),
        (300, "さんびゃく"),
        (600, "ろっぴゃく"),
        (3000, "さんぜん"),
        (8000, "はっせん"),
        (10000, "いちまん"),
        (2024, "にせんにじゅうよん"),
        (100000000, "いちおく"),
    ],
)
def test_japanese_number_reading(value, reading):
    assert n.japanese_number_reading(value) == reading


def test_katakana_to_hiragana_keeps_long_vowel():
    assert n.katakana_to_hiragana("ラーメン") == "らーめん"


def test_ja_reading_makes_kanji_and_kana_equal():
    assert n.normalize_ja_reading("漢字です") == n.normalize_ja_reading("かんじです")
    assert n.normalize_ja_reading("東京に行った") == "とうきょうにいった"


def test_ja_reading_converts_digits():
    assert n.normalize_ja_reading("2024年") == "にせんにじゅうよんねん"


# ---------------------------------------------------------------- Korean


def test_normalize_ko_keeps_spacing():
    assert n.normalize_ko("안녕하세요, 여러분! [음악]") == "안녕하세요 여러분"


def test_normalize_ko_nospace():
    assert n.normalize_ko_nospace("안녕 하세요.") == "안녕하세요"


# ---------------------------------------------------------------- metric specs


def test_metrics_for_names_and_primary():
    assert [(m.name, m.is_primary) for m in n.metrics_for("ja")] == [("cer", True), ("cer_reading", False)]
    assert [(m.name, m.is_primary) for m in n.metrics_for("es")] == [("wer", True), ("wer_no_accents", False)]
    assert [(m.name, m.is_primary) for m in n.metrics_for("ko")] == [
        ("cer_nospace", True),
        ("wer_syllable", False),
    ]


def test_tokenizers():
    ja, _ = n.metrics_for("ja")
    assert ja.tokenizer("あい う。") == ["あ", "い", "う"]
    es, _ = n.metrics_for("es")
    assert es.tokenizer("Hola, mundo") == ["hola", "mundo"]
    ko_cer, ko_wer = n.metrics_for("ko")
    assert ko_cer.tokenizer("안녕 하세요") == ["안", "녕", "하", "세", "요"]
    assert ko_wer.tokenizer("안녕 하세요") == ["안녕", "하세요"]


def test_metrics_for_unknown_language():
    with pytest.raises(ValueError):
        n.metrics_for("xx")


# ---------------------------------------------------------------- English


@pytest.mark.parametrize(
    "value,words",
    [
        (0, "zero"),
        (13, "thirteen"),
        (21, "twenty one"),
        (105, "one hundred five"),
        (1000, "one thousand"),
        (21000, "twenty one thousand"),
        (1234567, "one million two hundred thirty four thousand five hundred sixty seven"),
    ],
)
def test_english_number_words(value, words):
    assert n.english_number_words(value) == words


@pytest.mark.parametrize("value,words", [(1, "first"), (2, "second"), (12, "twelfth"), (22, "twenty second"), (40, "fortieth")])
def test_english_ordinal_words(value, words):
    assert n.english_ordinal_words(value) == words


def test_english_numbers_in_text():
    assert n.collapse_whitespace(n.english_numbers_to_words("1,000 cats, 25% on the 1st, 3.14")) == (
        "one thousand cats, twenty five percent on the first , three point one four"
    )


def test_strip_punctuation_keeps_inner_apostrophes():
    assert n.collapse_whitespace(n.strip_punctuation_keep_apostrophes("don’t 'quote' rock'n'roll!")) == (
        "don't quote rock'n'roll"
    )


def test_normalize_en_full():
    assert n.normalize_en("I DON'T have 3 cats. [Music] (laughs)") == "i don't have three cats"


# ---------------------------------------------------------------- Hindi


def test_normalize_hi_danda_digits_and_mixed_script():
    assert n.normalize_hi("मैं ठीक हूँ। Kya haal hai? २०२४॥") == n.nfkc("मैं ठीक हूँ kya haal hai 2024")


@pytest.mark.parametrize(
    "dev,latin",
    [("है", "hai"), ("क्या", "kya"), ("मैं", "main"), ("दोस्त", "dost"), ("ज़िंदगी", "zindagi"), ("मेरा", "mera")],
)
def test_transliterate_devanagari(dev, latin):
    assert n.transliterate_devanagari(n.nfkc(dev)) == latin


def test_loosen_latin():
    assert n.loosen_latin("theek hoon accha wala") == "thik hun acha vala"


def test_wer_translit_matches_across_scripts():
    _, translit = n.metrics_for("hi")
    assert translit.tokenizer("मैं ठीक हूँ, क्या हाल है?") == translit.tokenizer("main theek hoon kya haal hai")


def test_metrics_for_new_languages():
    assert [(m.name, m.is_primary) for m in n.metrics_for("en")] == [("wer", True)]
    assert [(m.name, m.is_primary) for m in n.metrics_for("hi")] == [("wer", True), ("wer_translit", False)]
