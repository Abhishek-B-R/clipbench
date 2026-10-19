from __future__ import annotations

import json

import pandas as pd
import pytest

from clipbench.scoring.cli import headline_table
from clipbench.scoring.score import duration_bucket, pairwise_disagreement, score_run

RUN = "test-run"
N_CLIPS = 20


def _ref_text(i: int) -> str:
    return "あいうえおかきくけこ" * (1 + i % 3)  # 10, 20 or 30 characters


def _write_run(settings) -> None:
    rows, texts, refs = [], [], []
    for i in range(N_CLIPS):
        vid = f"vid{i:02d}"
        ref = _ref_text(i)
        tags = ["background_music"] if i % 2 == 0 else []
        duration = [10.0, 20.0, 45.0, 75.0][i % 4]
        refs.append(
            {"video_id": vid, "tier": "creator", "ref_text": ref, "words_json": None, "tags": json.dumps(tags)}
        )
        hyps = {
            "good": ref,  # perfect
            "bad": "ん" + ref[1:-1],  # one substitution and one deletion
        }
        for provider, hyp in hyps.items():
            repeats = range(4) if provider == "good" and i < 3 else range(1)
            for rep in repeats:
                text = hyp if rep < 2 else hyp[:-1]  # repeat 2 and 3 drop the last character
                rows.append(
                    {
                        "run_id": RUN,
                        "provider": provider,
                        "model_id": f"{provider}-1",
                        "provider_version": None,
                        "video_id": vid,
                        "language": "ja",
                        "tier": "creator",
                        "repeat": rep,
                        "status": "ok",
                        "error": None,
                        "latency_ms": 1000.0 + 100 * rep,
                        "audio_seconds": duration,
                        "cost_usd": 0.01 if provider == "bad" else 0.0,
                        "cached": rep == 0 and i == 0,
                        "duration_s": duration,
                        "channel": "ch",
                        "tags": json.dumps(tags),
                        "hyp_chars": len(text),
                    }
                )
                texts.append({"provider": provider, "video_id": vid, "repeat": rep, "hyp_text": text, "words_json": None})
    # one skipped row that must not be scored
    rows.append({**rows[0], "provider": "skipper", "model_id": "s-1", "status": "skipped"})

    run_dir = settings.runs_dir / RUN
    text_dir = settings.runs_text_dir / RUN
    run_dir.mkdir(parents=True)
    text_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(run_dir / "rows.parquet", index=False)
    pd.DataFrame(texts).to_parquet(text_dir / "texts.parquet", index=False)
    pd.DataFrame(refs).to_parquet(text_dir / "refs.parquet", index=False)


@pytest.fixture
def scored(tmp_settings):
    _write_run(tmp_settings)
    out = score_run(RUN, tmp_settings)
    return {name: pd.read_parquet(out / f"{name}.parquet") for name in ("scores", "aggregates", "paired", "stability")}


def test_output_location(tmp_settings):
    _write_run(tmp_settings)
    out = score_run(RUN, tmp_settings)
    assert out == tmp_settings.results_dir / RUN
    for name in ("scores", "aggregates", "paired", "stability"):
        assert (out / f"{name}.parquet").exists()


def test_scores(scored):
    s = scored["scores"]
    assert set(s["provider"]) == {"good", "bad"}  # skipped provider not scored
    assert len(s) == N_CLIPS * 2 * 2  # clips x providers x metrics, repeat 0 only
    assert set(s["metric"]) == {"cer", "cer_reading"}
    good = s[(s["provider"] == "good") & (s["metric"] == "cer")]
    assert (good["errors"] == 0).all()
    bad = s[(s["provider"] == "bad") & (s["metric"] == "cer") & (s["video_id"] == "vid00")].iloc[0]
    assert (bad["ref_len"], bad["subs"], bad["dels"], bad["ins"], bad["errors"]) == (10, 1, 1, 0, 2)
    assert bad["error_rate"] == pytest.approx(0.2)
    assert bad["duration_bucket"] == "<15s"
    assert json.loads(bad["tags"]) == ["background_music"]
    assert bool(s.loc[s["metric"] == "cer", "is_primary"].all())


def test_aggregates(scored):
    a = scored["aggregates"]
    all_cer = a[(a["metric"] == "cer") & (a["slice_kind"] == "all")].set_index("provider")
    assert all_cer.loc["good", "error_rate"] == 0
    total_ref = sum(len(_ref_text(i)) for i in range(N_CLIPS))
    assert all_cer.loc["bad", "error_rate"] == pytest.approx(2 * N_CLIPS / total_ref)
    assert all_cer.loc["bad", "n_clips"] == N_CLIPS
    assert not all_cer.loc["bad", "too_small"]
    audio_hours = sum([10.0, 20.0, 45.0, 75.0][i % 4] for i in range(N_CLIPS)) / 3600
    assert all_cer.loc["bad", "audio_hours"] == pytest.approx(audio_hours)
    # list cost counts even for the cached row
    assert all_cer.loc["bad", "cost_usd_total"] == pytest.approx(0.01 * N_CLIPS)
    assert all_cer.loc["bad", "cost_per_hour"] == pytest.approx(0.01 * N_CLIPS / audio_hours)
    assert all_cer.loc["bad", "latency_p50"] == 1000
    assert all_cer.loc["bad", "ci_low"] <= all_cer.loc["bad", "error_rate"] <= all_cer.loc["bad", "ci_high"]

    tag = a[(a["slice_kind"] == "tag") & (a["metric"] == "cer")]
    assert set(tag["slice"]) == {"background_music"}
    assert (tag["n_clips"] == N_CLIPS // 2).all()
    assert tag["too_small"].all()
    dur = a[(a["slice_kind"] == "duration") & (a["metric"] == "cer")]
    assert set(dur["slice"]) == {"<15s", "15-30s", "30-60s", "60-90s"}


def test_paired(scored):
    p = scored["paired"]
    row = p[(p["metric"] == "cer") & (p["slice_kind"] == "all")].iloc[0]
    assert (row["provider_a"], row["provider_b"]) == ("bad", "good")
    assert row["n_clips"] == N_CLIPS
    assert row["diff"] > 0
    assert row["verdict"] == "b_better"
    assert set(p["tier"]) == {"creator"}


def test_stability(scored):
    st = scored["stability"].set_index("video_id")
    assert set(st.index) == {"vid00", "vid01", "vid02"}
    assert (st["n_repeats"] == 3).all()
    # repeats 1..3: one full text, two missing the last char; pairs: (1,2), (1,3) differ by 1 token, (2,3) equal
    vid00_len = len(_ref_text(0))
    assert st.loc["vid00", "text_disagreement"] == pytest.approx((2 / vid00_len) / 3)
    assert st.loc["vid00", "latency_mean"] == pytest.approx(1200)


def test_headline_table_labels_creator(scored):
    table = headline_table(scored["aggregates"], RUN)
    assert table is not None
    assert "unverified reference" in str(table.title)
    assert table.row_count == 2


def test_duration_bucket():
    assert duration_bucket(14.9) == "<15s"
    assert duration_bucket(15) == "15-30s"
    assert duration_bucket(59) == "30-60s"
    assert duration_bucket(120) == "60-90s"
    assert duration_bucket(None) is None


def test_pairwise_disagreement_symmetric():
    assert pairwise_disagreement(["a", "b"], ["a"]) == pairwise_disagreement(["a"], ["a", "b"]) == 0.5


# ---------------------------------------------------------------- v2 metrics

V2_RUN = "v2-run"


def _words(text: str, start: int = 2000, step: int = 500) -> list[dict]:
    return [{"text": w, "start_ms": start + i * step, "end_ms": start + i * step + 400} for i, w in enumerate(text.split())]


def _write_v2_run(settings) -> None:
    rows, texts, refs = [], [], []
    ref_text = "Hello there, my friend. How are you?"
    for i in range(4):
        vid = f"en{i}"
        music = i % 2 == 0
        refs.append(
            {
                "video_id": vid,
                "tier": "verified",
                "ref_text": ref_text,
                "words_json": json.dumps(_words(ref_text)),
                "tags": json.dumps([]),
                "category": "vlog_music" if music else "talking_head",
                "nonspeech_spans_json": json.dumps([{"start_ms": 0, "end_ms": 2000, "kind": "music"}] if music else []),
                "codeswitch_spans_json": json.dumps([{"start": 13, "end": 22, "lang": "en"}]),
                "punctuated": True,
                "timing_source": "aligner:test",
            }
        )
        hyps = {
            "a": (ref_text, _words(ref_text, start=2100)),  # perfect text, 100 ms late
            "b": ("la la hello there my friend how are you", _words("la la", start=500, step=600)
                  + _words("hello there my friend how are you", start=2000)),  # fmt: skip
        }
        for provider, (text, words) in hyps.items():
            rows.append(
                {
                    "run_id": V2_RUN, "provider": provider, "model_id": f"{provider}-1", "provider_version": None,
                    "video_id": vid, "language": "en", "tier": "verified", "repeat": 0, "status": "ok", "error": None,
                    "latency_ms": 500.0, "audio_seconds": 6.0, "cost_usd": 0.0, "cached": False, "duration_s": 6.0,
                    "channel": "ch", "tags": "[]", "hyp_chars": len(text),
                }  # fmt: skip
            )
            texts.append({"provider": provider, "video_id": vid, "repeat": 0, "hyp_text": text, "words_json": json.dumps(words)})
    (settings.runs_dir / V2_RUN).mkdir(parents=True)
    (settings.runs_text_dir / V2_RUN).mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(settings.runs_dir / V2_RUN / "rows.parquet", index=False)
    pd.DataFrame(texts).to_parquet(settings.runs_text_dir / V2_RUN / "texts.parquet", index=False)
    pd.DataFrame(refs).to_parquet(settings.runs_text_dir / V2_RUN / "refs.parquet", index=False)


def test_v2_scores_and_aggregates(tmp_settings):
    _write_v2_run(tmp_settings)
    out = score_run(V2_RUN, tmp_settings)
    s = pd.read_parquet(out / "scores.parquet").set_index(["provider", "video_id"])
    a0, b0, b1 = s.loc[("a", "en0")], s.loc[("b", "en0")], s.loc[("b", "en1")]

    assert a0["timing_n_words"] == 7 and a0["timing_median_ms"] == 100 and a0["timing_within_200ms"] == 1.0
    assert b0["timing_median_ms"] == 0

    # en0 has a tagged 2 s music span; en1 falls back to the 2 s lead in before the first word
    assert a0["nonspeech_minutes"] == pytest.approx(2 / 60)
    assert a0["halluc_nonspeech_words"] == 0 and b0["halluc_nonspeech_words"] == 2
    assert b1["halluc_nonspeech_words"] == 2
    assert b0["halluc_per_nonspeech_min"] == pytest.approx(60.0)

    assert a0["punct_f1"] == 1.0 and a0["casing_acc"] == 1.0
    assert b0["punct_f1"] == 0.0 and b0["casing_acc"] < 1.0

    assert a0["codeswitch_ref_len"] == 2 and a0["codeswitch_error_rate"] == 0
    assert a0["category"] == "vlog_music" and a0["audio_condition"] == "music"
    assert s.loc[("a", "en1"), "audio_condition"] == "no_music"

    agg = pd.read_parquet(out / "aggregates.parquet")
    all_a = agg[(agg["provider"] == "a") & (agg["slice_kind"] == "all")].iloc[0]
    assert all_a["timing_n_clips"] == 4 and all_a["timing_median_ms"] == 100
    assert all_a["punct_f1"] == 1.0 and all_a["readiness_n_clips"] == 4
    all_b = agg[(agg["provider"] == "b") & (agg["slice_kind"] == "all")].iloc[0]
    assert all_b["halluc_per_nonspeech_min"] == pytest.approx(60.0)
    assert set(agg.loc[agg["slice_kind"] == "category", "slice"]) == {"vlog_music", "talking_head"}
    assert set(agg.loc[agg["slice_kind"] == "audio_condition", "slice"]) == {"music", "no_music"}
    paired = pd.read_parquet(out / "paired.parquet")
    assert {"category", "audio_condition"} <= set(paired["slice_kind"])


def test_v1_refs_without_v2_columns_still_score(scored):
    s = scored["scores"]
    assert s["timing_n_words"].isna().all()
    assert s["punct_f1"].isna().all()
    assert s["codeswitch_ref_len"].isna().all()
    assert (s["audio_condition"].isin(["music", "no_music"])).all()


def test_mixed_language_run_is_refused(tmp_settings):
    """en, es and hi all score `wer`: one run with two languages would pool them into one number."""
    _write_v2_run(tmp_settings)
    path = tmp_settings.runs_dir / V2_RUN / "rows.parquet"
    rows = pd.read_parquet(path)
    rows.loc[rows["video_id"] == "en1", "language"] = "es"
    rows.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="one language"):
        score_run(V2_RUN, tmp_settings)


def test_empty_output_over_nonspeech_is_zero_hallucination(tmp_settings):
    """A provider that writes nothing hallucinated nothing: the clip counts with 0 words, it is not
    dropped (dropping it would shrink that provider's non-speech minutes and inflate its rate)."""
    _write_v2_run(tmp_settings)
    path = tmp_settings.runs_text_dir / V2_RUN / "texts.parquet"
    texts = pd.read_parquet(path)
    mask = (texts["provider"] == "a") & (texts["video_id"] == "en0")
    texts.loc[mask, "hyp_text"] = ""
    texts.loc[mask, "words_json"] = "[]"
    texts.to_parquet(path, index=False)
    out = score_run(V2_RUN, tmp_settings)
    s = pd.read_parquet(out / "scores.parquet").set_index(["provider", "video_id"])
    a0 = s.loc[("a", "en0")].iloc[0] if isinstance(s.loc[("a", "en0")], pd.DataFrame) else s.loc[("a", "en0")]
    assert a0["halluc_nonspeech_words"] == 0
    assert a0["halluc_per_nonspeech_min"] == 0
    agg = pd.read_parquet(out / "aggregates.parquet")
    all_a = agg[(agg["provider"] == "a") & (agg["slice_kind"] == "all")].iloc[0]
    assert all_a["nonspeech_n_clips"] == 4


def test_empty_output_without_timings_stays_unmeasured(tmp_settings):
    """A provider with no word timings at all gets no non-speech number, not a 0 from its empty clips."""
    _write_v2_run(tmp_settings)
    path = tmp_settings.runs_text_dir / V2_RUN / "texts.parquet"
    texts = pd.read_parquet(path)
    texts.loc[texts["provider"] == "a", "words_json"] = None
    texts.loc[(texts["provider"] == "a") & (texts["video_id"] == "en0"), "hyp_text"] = ""
    texts.to_parquet(path, index=False)
    out = score_run(V2_RUN, tmp_settings)
    agg = pd.read_parquet(out / "aggregates.parquet")
    all_a = agg[(agg["provider"] == "a") & (agg["slice_kind"] == "all")].iloc[0]
    assert all_a["nonspeech_n_clips"] == 0 and pd.isna(all_a["halluc_per_nonspeech_min"])
