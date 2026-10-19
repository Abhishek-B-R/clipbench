from __future__ import annotations

from clipbench.models import CodeSwitchSpan
from clipbench.scoring.codeswitch import codeswitch_error, flagged_tokens
from clipbench.scoring.normalize import metrics_for

ES = metrics_for("es")[0]
JA = metrics_for("ja")[0]


def span(a, b):
    return CodeSwitchSpan(start=a, end=b, lang="en")


def test_flagged_tokens_words_snap_to_word_boundaries():
    text = "hola my friend que tal"
    # span cuts into 'my' and 'friend': widened to whole words
    tokens, flags = flagged_tokens(text, [span(6, 12)], ES)
    assert tokens == ["hola", "my", "friend", "que", "tal"]
    assert flags == [False, True, True, False, False]


def test_flagged_tokens_characters():
    text = "今日はhappyです"
    tokens, flags = flagged_tokens(text, [span(3, 8)], JA)
    assert "".join(t for t, f in zip(tokens, flags, strict=True) if f) == "happy"
    assert sum(flags) == 5


def test_codeswitch_error_hand_computed():
    text = "hola my friend que tal"
    hyp = ES.tokenizer("hola mi friend que tal")  # my -> mi
    cs = codeswitch_error(text, [span(5, 14)], hyp, ES)
    assert (cs.ref_len, cs.errors) == (2, 1)
    assert cs.error_rate == 0.5


def test_codeswitch_errors_outside_span_not_counted():
    text = "hola my friend que tal"
    hyp = ES.tokenizer("ola my friend ke tal")
    cs = codeswitch_error(text, [span(5, 14)], hyp, ES)
    assert cs.errors == 0


def test_codeswitch_insertion_inside_span():
    text = "hola my friend que tal"
    hyp = ES.tokenizer("hola my good friend que tal")
    assert codeswitch_error(text, [span(5, 14)], hyp, ES).errors == 1
    # insertion at the span edge (between 'friend' and 'que') is not counted
    hyp = ES.tokenizer("hola my friend uh que tal")
    assert codeswitch_error(text, [span(5, 14)], hyp, ES).errors == 0


def test_codeswitch_none_without_spans():
    assert codeswitch_error("hola", [], ["hola"], ES) is None
