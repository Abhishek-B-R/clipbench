from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench.analysis import errors as E


def _local_align(ref, hyp):
    """Tiny Levenshtein fallback, used only if clipbench.scoring.metrics is missing."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    ops, i, j = [], n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("equal" if ref[i - 1] == hyp[j - 1] else "sub", ref[i - 1], hyp[j - 1], i - 1))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", ref[i - 1], None, i - 1))
            i -= 1
        else:
            ops.append(("ins", None, hyp[j - 1], i))
            j -= 1
    return {"ops": ops[::-1]}


@pytest.fixture
def align_fn():
    try:
        return E.get_align()
    except ImportError:
        return _local_align


def _write_run(settings, run_id, language, refs, hyps):
    d = settings.runs_text_dir / run_id
    d.mkdir(parents=True)
    pd.DataFrame(
        [
            {"video_id": v, "tier": "verified", "ref_text": t, "words_json": None, "tags": "[]"}
            for v, t in refs.items()
        ]
    ).to_parquet(d / "refs.parquet")
    pd.DataFrame(
        [
            {"provider": p, "video_id": v, "repeat": 0, "hyp_text": t, "words_json": None}
            for p, per in hyps.items()
            for v, t in per.items()
        ]
    ).to_parquet(d / "texts.parquet")
    rd = settings.runs_dir / run_id
    rd.mkdir(parents=True)
    pd.DataFrame(
        [
            {"provider": p, "video_id": v, "repeat": 0, "status": "ok", "language": language}
            for p, per in hyps.items()
            for v in per
        ]
    ).to_parquet(rd / "rows.parquet")


def test_es_substitutions_and_all_fail(tmp_settings, align_fn):
    refs = {
        "v1": "hola que tal estas hoy amigo mio",
        "v2": "vamos a la playa con mis amigos",
        "v3": "uno dos tres cuatro cinco seis",
    }
    hyps = {
        "a": {
            "v1": "ola que tal estas hoy amigo mio",
            "v2": "vamos a la plaza con mis amigos",
            "v3": "x y z",
        },
        "b": {"v1": "ola que tal estas hoy amigo mio", "v2": "vamos a la playa con mis amigos", "v3": "q w"},
        "c": {
            "v1": "hola que tal estas hoy amigo mio",
            "v2": "vamos a la playa con mis amigos",
            "v3": "nada",
        },
    }
    _write_run(tmp_settings, "r1", "es", refs, hyps)
    res = E.run_errors("r1", tmp_settings, align_fn=align_fn)

    assert res["providers"]["a"]["top_substitutions"][0][2] == 1
    pairs = {(r, h): n for r, h, n in res["providers"]["a"]["top_substitutions"]}
    assert pairs[("hola", "ola")] == 1 and pairs[("playa", "plaza")] == 1
    assert {(r, h) for r, h, _ in res["providers"]["b"]["top_substitutions"]} >= {("hola", "ola")}
    assert res["all_fail"] == ["v3"]
    assert json.loads((tmp_settings.data_dir / "rereview.json").read_text()) == ["v3"]
    written = json.loads((tmp_settings.results_dir / "r1" / "errors.json").read_text())
    assert written["all_fail"] == ["v3"]
    # worst clip for "a" relative to the others is v2 (others are perfect there)
    assert res["providers"]["a"]["worst_clips"][0]["video_id"] in {"v2", "v3"}
    for ex in written["examples"]:
        assert len(ex["ref_snippet"].split()) <= 4 and len(ex["hyp_snippet"].split()) <= 4


def test_ja_spans_grouped(tmp_settings, align_fn):
    refs = {"j1": "きょうはいいてんきですね", "j2": "わたしはがくせいです"}
    hyps = {
        "a": {"j1": "今日はいいてんきですね", "j2": "わたしはがくせいです"},
        "b": {"j1": "きょうはいいてんきですね", "j2": "私はがくせいです"},
    }
    _write_run(tmp_settings, "r2", "ja", refs, hyps)
    res = E.run_errors("r2", tmp_settings, align_fn=align_fn)
    a = {(r, h): n for r, h, n in res["providers"]["a"]["top_substitutions"]}
    b = {(r, h): n for r, h, n in res["providers"]["b"]["top_substitutions"]}
    assert a == {("きょう", "今日"): 1}
    assert b == {("わたし", "私"): 1}
    assert res["all_fail"] == []
    for ex in res["examples"]:
        assert len(ex["ref_snippet"]) <= 8 and len(ex["hyp_snippet"]) <= 8
        assert ex["ref_snippet"] != refs[ex["video_id"]]


def test_snippet_never_full_caption():
    ops = [("sub", "a", "b"), ("eq", "c", "c")]
    ref, hyp = E.snippet(ops, (0, 1), False, 2)
    assert ref == "a" and hyp == "b"


def test_rereview_keeps_clips_of_other_runs(tmp_settings, align_fn):
    """errors on one run (say en) must not wipe the re-review queue another run (ja) wrote; clips of this
    run are replaced by this run's verdict."""
    refs = {"v1": "hola que tal estas hoy", "v2": "uno dos tres cuatro"}
    hyps = {"a": {"v1": "hola que tal estas hoy", "v2": "x"}, "b": {"v1": "hola que tal estas hoy", "v2": "y"}}
    _write_run(tmp_settings, "r1", "es", refs, hyps)
    tmp_settings.data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_settings.data_dir / "rereview.json").write_text(json.dumps(["jaclip1", "v1"]))
    E.run_errors("r1", tmp_settings, align_fn=align_fn)
    assert json.loads((tmp_settings.data_dir / "rereview.json").read_text()) == ["jaclip1", "v2"]
