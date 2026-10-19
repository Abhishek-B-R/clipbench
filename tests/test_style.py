from __future__ import annotations

import pandas as pd

from clipbench.scoring.style import clip_style, is_filler, repetitions, score_style, style_tokens
from tests.quality_helpers import write_run


def _fillers(text: str, lang: str) -> list[str]:
    toks = style_tokens(text, lang)
    return [t.key for i, t in enumerate(toks) if is_filler(toks, i, lang)]


def test_filler_lists_per_language():
    assert _fillers("Um, I think, uh, it is like, fine", "en") == ["um", "uh", "like"]
    assert _fillers("I like it", "en") == []
    assert _fillers("este, no sé. Este libro es bueno", "es") == ["este"]
    assert _fillers("음 그, 사람이 그 사람", "ko") == ["음", "그"]
    assert _fillers("matlab, main ghar gaya. iska matlab kya hai", "hi") == ["matlab"]
    assert _fillers("えーと、あの、あの人は", "ja") == ["えーと", "あの"]


def test_repetitions_and_stutters():
    toks = style_tokens("I I think b-but it is is fine", "en")
    assert [toks[i].key for i in repetitions(toks, "en")] == ["i", "but", "is"]
    toks = style_tokens("わ、わたしは", "ja")
    assert len(repetitions(toks, "ja")) == 1


def test_clip_style_keep_and_drop():
    ref = "Um, I I think that's, uh, fucking great"
    verbatim = clip_style(ref, "um I I think that's uh fucking great", "en", 0.5)
    assert verbatim["ref_fillers"] == 2 and verbatim["fillers_kept"] == 2
    assert verbatim["repetitions_kept"] == 1 and verbatim["prof_written"] == 1
    assert verbatim["hyp_fillers_per_min"] == 4.0
    clean = clip_style(ref, "I think that's f***ing great", "en", 0.5)
    assert clean["fillers_kept"] == 0 and clean["filler_keep_rate"] == 0
    assert clean["repetitions_kept"] == 0 and clean["prof_masked"] == 1 and clean["hyp_masked"] == 1
    dropped = clip_style(ref, "I think that's great", "en", 0.5)
    assert dropped["prof_dropped"] == 1
    yt = clip_style("this is shit", "this is [ __ ]", "en", 0.1)
    assert yt["prof_masked"] == 1
    bleep = clip_style("what the [bleep] man", "what the man", "en", 0.1)
    assert bleep["ref_bleeps"] == 1 and bleep["bleep_dropped"] == 1


def test_score_style_aggregates(tmp_settings):
    ref = "um so uh I I went there um okay"
    clips = [{"text": ref, "seconds": 30.0} for _ in range(6)]
    write_run(tmp_settings, "st", "en", clips, {"verbatim": [ref] * 6, "clean": ["so I went there okay"] * 6})
    out = score_style("st", tmp_settings)
    assert len(pd.read_parquet(out)) == 12
    agg = pd.read_parquet(out.with_name("style_aggregates.parquet")).set_index("provider")
    allrows = agg[agg.slice_kind == "all"]
    assert allrows.loc["verbatim", "filler_keep_rate"] == 1.0
    assert allrows.loc["verbatim", "filler_style"] == "keeps"
    assert allrows.loc["clean", "filler_style"] == "drops"
    assert allrows.loc["clean", "hyp_fillers_per_min"] == 0
    assert allrows.loc["verbatim", "hyp_fillers_per_min"] == 6.0
