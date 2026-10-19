from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest

from clipbench.judge.client import (
    DEFAULT_MODEL,
    JudgeError,
    SemanticJudge,
    estimate_pair_usd,
    load_prompt,
    parse_answer,
)
from clipbench.judge.run import (
    MIN_PAIRS,
    agreement,
    calibrate,
    judge_run,
    label_session,
    labels_path,
    load_labels,
    parse_label,
    spearman,
)
from clipbench.providers.cache import ResultCache
from clipbench.scoring.semantic import export_payload, publish_status
from tests.quality_helpers import write_run


def _answer(score, reason="ok", usage=True, shape="choices"):
    content = json.dumps({"score": score, "reason": reason})
    result = {"choices": [{"message": {"content": content}}]} if shape == "choices" else {"response": content}
    if usage:
        result["usage"] = {"prompt_tokens": 500, "completion_tokens": 20}
    return {"success": True, "result": result}


class Recorder:
    """MockTransport handler: scores by a rule, counts requests, can fail first."""

    def __init__(self, rule=lambda ref, hyp: 1.0 if ref == hyp else 0.25, fail_first: int = 0, bad_first: int = 0):
        self.rule, self.fail_first, self.bad_first = rule, fail_first, bad_first
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if len(self.requests) <= self.fail_first:
            return httpx.Response(503, json={"success": False})
        if len(self.requests) <= self.fail_first + self.bad_first:
            return httpx.Response(200, json={"success": True, "result": {"response": "I think it is fine"}})
        user = body["messages"][1]["content"]
        ref = user.split("Reference:\n")[1].split("\n\nHypothesis:")[0]
        hyp = user.split("Hypothesis:\n")[1]
        return httpx.Response(200, json=_answer(self.rule(ref, hyp)))


def test_prompt_is_pinned_and_rendered():
    p = load_prompt()
    assert p.version == "semantic_v1" and len(p.hash) == 12
    msgs = p.render("ja", "今日は", "きょうは")
    assert msgs[0]["role"] == "system" and "Japanese" in msgs[1]["content"] and "きょうは" in msgs[1]["content"]
    assert estimate_pair_usd(DEFAULT_MODEL, "a" * 300, "b" * 300) < 0.001


def test_parse_answer_shapes_and_validation():
    assert parse_answer({"response": '{"score": 0.5, "reason": "partial"}'}) == (0.5, "partial")
    assert parse_answer({"response": {"score": 1, "reason": "same"}}) == (1.0, "same")
    assert parse_answer({"choices": [{"message": {"content": '```json\n{"score": 0, "reason": "a\\nb"}\n```'}}]}) == (
        0.0,
        "a b",
    )
    assert parse_answer({"response": '<think>hmm {"x":1}</think>{"score": 0.75, "reason": "minor"}'})[0] == 0.75
    for bad in ['{"score": 2, "reason": "x"}', '{"score": true, "reason": "x"}', '{"score": 0.5}', "no json"]:
        with pytest.raises(ValueError):
            parse_answer({"response": bad})


async def test_judge_retries_caches_and_budget(tmp_settings):
    from clipbench.budget import BudgetGuard

    rec = Recorder(fail_first=1, bad_first=1)
    guard = BudgetGuard(tmp_settings.spend_path, 1.0, "t")
    judge = SemanticJudge(ResultCache(tmp_settings.cache_dir), guard, transport=httpx.MockTransport(rec),
                          retry_sleep_s=0)  # fmt: skip
    s = await judge.score("en", "hello there", "hello there")
    assert s.score == 1.0 and s.attempts == 3 and not s.cached
    assert rec.requests[0]["temperature"] == 0
    assert guard.spent > 0
    again = await judge.score("en", "hello there", "hello there")
    assert again.cached and len(rec.requests) == 3
    await judge.aclose()
    # every attempt fails: JudgeError, nothing cached
    always_bad = Recorder(bad_first=10)
    j2 = SemanticJudge(ResultCache(tmp_settings.cache_dir), None, transport=httpx.MockTransport(always_bad),
                       retry_sleep_s=0)  # fmt: skip
    with pytest.raises(JudgeError):
        await j2.score("en", "x", "y")
    assert j2.cached("x", "y") is None
    await j2.aclose()


async def test_budget_cap_stops_the_judge(tmp_settings):
    from clipbench.budget import BudgetExceeded, BudgetGuard

    guard = BudgetGuard(tmp_settings.spend_path, 0.0, "t")
    judge = SemanticJudge(ResultCache(tmp_settings.cache_dir), guard, transport=httpx.MockTransport(Recorder()))
    with pytest.raises(BudgetExceeded):
        await judge.score("en", "a", "b")
    await judge.aclose()


def _run(tmp_settings, n=4):
    clips = [{"text": f"we went to the park on day {i}", "conditions": ["music"] if i % 2 else []} for i in range(n)]
    hyps = {"good": [c["text"] for c in clips], "bad": ["something else" for _ in clips]}
    write_run(tmp_settings, "sem", "en", clips, hyps)


def test_judge_run_dry_run_writes_nothing(tmp_settings):
    _run(tmp_settings)
    res = judge_run("sem", tmp_settings, dry_run=True)
    assert res.plan.n_pairs == 8 and res.plan.n_cached == 0 and res.plan.est_usd > 0
    assert not (tmp_settings.results_dir / "sem" / "semantic.parquet").exists()


def test_judge_run_without_credentials_refuses(tmp_settings, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    _run(tmp_settings)
    with pytest.raises(RuntimeError):
        judge_run("sem", tmp_settings)


def test_judge_run_writes_semantic_files_without_text(tmp_settings):
    _run(tmp_settings)
    rec = Recorder()
    res = judge_run("sem", tmp_settings, transport=httpx.MockTransport(rec))
    assert res.n_scored == 8 and res.n_failed == 0 and len(rec.requests) == 8
    df = pd.read_parquet(tmp_settings.results_dir / "sem" / "semantic.parquet")
    assert set(df.columns).isdisjoint({"ref", "hyp", "reason", "hyp_text"})
    assert df[df.provider == "good"]["semantic_score"].eq(1.0).all()
    agg = pd.read_parquet(tmp_settings.results_dir / "sem" / "semantic_aggregates.parquet")
    row = agg[(agg.provider == "bad") & (agg.slice_kind == "all")].iloc[0]
    assert row["semantic_score"] == 0.25 and row["semantic_error"] == 0.75
    assert (tmp_settings.runs_text_dir / "sem" / "semantic_reasons.parquet").exists()
    # second run is fully cached
    rec2 = Recorder()
    judge_run("sem", tmp_settings, transport=httpx.MockTransport(rec2))
    assert rec2.requests == []
    # not calibrated: export refuses to publish and says why
    payload = export_payload("sem", tmp_settings)
    assert payload["published"] is False and payload["aggregates"] == []
    assert "calibration" in payload["reason"]


def test_parse_label_and_label_session(tmp_settings):
    assert parse_label("0.75 missed a name") == ("score", 0.75, "missed a name")
    assert parse_label("s")[0] == "skip" and parse_label("q")[0] == "quit" and parse_label("7")[0] == "invalid"
    _run(tmp_settings)
    answers = iter(["1", "huh", "0.5 partial", "s", "q"])
    shown: list[str] = []
    saved = label_session(tmp_settings, "en", "abhishek", n=10, ask=lambda _: next(answers), show=shown.append)
    assert saved == 2
    labels = load_labels(tmp_settings)
    assert list(labels["human_score"]) == [1.0, 0.5] and list(labels["note"]) == ["", "partial"]
    assert labels_path(tmp_settings).is_relative_to(tmp_settings.data_dir)
    # labelled pairs are not offered again to the same person
    answers2 = iter(["q"])
    label_session(tmp_settings, "en", "abhishek", n=10, ask=lambda _: next(answers2), show=shown.append)
    assert any(s.startswith("6 unlabelled en pairs; 2 en labels") for s in shown)


def test_spearman_and_agreement_gate():
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    good = agreement([0, 0.5, 1] * 20, [0, 0.5, 1] * 20)
    assert good["passes"] and good["n_pairs"] == 60
    few = agreement([0, 1], [0, 1])
    assert not few["passes"] and f"needs {MIN_PAIRS}" in few["reason"]
    bad = agreement([0, 0.5, 1] * 20, [1, 0.5, 0] * 20)
    assert not bad["passes"] and "Spearman" in bad["reason"]


def test_calibrate_and_publish(tmp_settings):
    # labels typed by a person in this test stand in for the human labeller
    path = labels_path(tmp_settings)
    path.parent.mkdir(parents=True)
    rows = []
    for i in range(60):
        same = i % 2 == 0
        ref = f"reference sentence {i}"
        rows.append({"lang": "en", "ref": ref, "hyp": ref if same else f"other {i}", "human_score": 1.0 if same else 0.0,
                     "labeller": "tester", "note": "", "pair_id": f"p{i}", "run_id": "x", "provider": "p",
                     "video_id": f"v{i}", "labelled_at": "2026-10-19T00:00:00Z"})  # fmt: skip
    pd.DataFrame(rows).to_csv(path, index=False)
    dry = calibrate(tmp_settings, dry_run=True)
    assert dry.calibration is None and dry.plan.n_unique == 60
    res = calibrate(tmp_settings, transport=httpx.MockTransport(Recorder()))
    st = res.calibration["languages"]["en"]
    assert st["n_pairs"] == 60 and st["passes"] and st["spearman"] == pytest.approx(1.0)
    ok, why = publish_status(res.calibration, "en", res.calibration["judge_model"], res.calibration["prompt_version"])
    assert ok, why
    assert not publish_status(res.calibration, "ja", res.calibration["judge_model"], res.calibration["prompt_version"])[0]
    assert not publish_status(res.calibration, "en", "other-model", res.calibration["prompt_version"])[0]
    _run(tmp_settings)
    judge_run("sem", tmp_settings, transport=httpx.MockTransport(Recorder()))
    payload = export_payload("sem", tmp_settings)
    assert payload["published"] and payload["aggregates"]
    assert payload["calibration"]["n_pairs"] == 60
    assert json.loads((tmp_settings.results_dir / "sem" / "judge_calibration.json").read_text())["languages"]["en"]
