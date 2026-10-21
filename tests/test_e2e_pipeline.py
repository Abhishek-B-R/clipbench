"""End to end: the whole local pipeline on a synthetic world, with no network, keys or real media.

dataset spec + caption store -> `data build` -> `data sample` -> `data check` -> audio on disk ->
verified references (review store) -> runner (fake providers: cache, resume, budget) -> `score` ->
`errors` -> `report` -> `export` -> `publish --check`.

Every id, caption and WAV is invented (tests/fixtures/e2e/synth.py). The Typer CLI is driven with
CliRunner wherever a command exists; the runner is called directly because the CLI cannot pick fake
providers. Not covered: anything that needs the network or a person (fetch-info, fetch-audio, discover,
fetch-tracks, real provider APIs, the review UI's HTTP server and browser, forced alignment models).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest
from rich.console import Console
from typer.testing import CliRunner

from clipbench.budget import BUDGET_ERROR, BudgetExceeded, load_spend
from clipbench.cli import app
from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Result
from clipbench.providers.base import ProviderSkip
from clipbench.providers.fake import FakeProvider
from clipbench.runner import REF_COLUMNS, ROW_COLUMNS, TEXT_COLUMNS, run
from tests.fixtures.e2e import synth
from tests.fixtures.e2e.synth import CATEGORIES6, SynthClip

LANG = "es"
cli = CliRunner()


# ---------------------------------------------------------------- environment


@pytest.fixture
def world(tmp_path, monkeypatch) -> Settings:
    """Every path the code reads or writes points into tmp_path, including the Vako store and web/."""
    env = {
        "CLIPBENCH_DATA_DIR": tmp_path / "data",
        "CLIPBENCH_RESULTS_DIR": tmp_path / "results",
        "CLIPBENCH_SITE_DIR": tmp_path / "site",
        "CLIPBENCH_WEB_DIR": tmp_path / "web",
        "CLIPBENCH_DATASETS_DIR": tmp_path / "datasets",
        "CLIPBENCH_VAKO_ROOT": tmp_path / "vako",  # empty: the real caption store is never read
        "CLIPBENCH_CANARY_DIR": tmp_path / "canary",
        "CLIPBENCH_SUBMISSIONS_DIR": tmp_path / "submissions",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CLIPBENCH_LANGUAGE", LANG)
    monkeypatch.setenv("CLIPBENCH_BUDGET_USD", "10")
    monkeypatch.setenv("CLIPBENCH_MIN_PROVIDER_COVERAGE", "0.9")
    monkeypatch.setenv("CLIPBENCH_REVIEWER", "e2e-test")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def invoke(*args: str, ok: bool = True):
    res = cli.invoke(app, list(args))
    if ok:
        assert res.exit_code == 0, f"clipbench {' '.join(args)} failed:\n{res.output}\n{res.exception!r}"
    return res


# ---------------------------------------------------------------- the synthetic dataset


def small_world() -> list[SynthClip]:
    """9 channels: eight with 3 Shorts each, one ("UCe2ebig") with 10 Shorts so the 5% cap bites, one
    window from a long video, plus four clips that must never be eligible."""
    clips: list[SynthClip] = []
    seed = 100
    for ch in range(8):
        for k in range(3):
            seed += 1
            clips.append(
                SynthClip(
                    f"e2es{ch:02d}{k:02d}xxx",
                    f"UCe2ech{ch:02d}",
                    duration=6.0 + (seed % 4),
                    category=CATEGORIES6[ch % 6],
                    seed=seed,
                )
            )
    for k in range(10):
        seed += 1
        # blank category: guessed from the title ("receta" -> cooking_tutorial)
        clips.append(SynthClip(f"e2ebig{k:03d}xx", "UCe2ebig", 7.0, title="receta rapida", seed=seed))
    clips.append(
        SynthClip("e2ewindow01", "UCe2ewin", 400.0, category="news_explainer", start_ms=60_000, end_ms=72_000, seed=900)
    )
    clips += [
        SynthClip("e2enocap001", "UCe2eodd", 8.0, category="gaming", creator=False, seed=901),  # ASR only
        SynthClip("e2elong0001", "UCe2eodd", 300.0, category="gaming", seed=902),  # long, not a window
        SynthClip("e2etiny0001", "UCe2eodd", 3.0, category="gaming", seed=903),  # under 5 s
        SynthClip("e2etrans001", "UCe2eodd", 8.0, category="gaming", translated_only=True, seed=904),
    ]
    return clips


def large_world() -> list[SynthClip]:
    """40 channels x 3 Shorts, six categories: passes every diversity rule once verified."""
    clips = []
    for ch in range(40):
        for k in range(3):
            seed = 2_000 + ch * 10 + k
            clips.append(
                SynthClip(f"e2eL{ch:03d}{k:02d}xx", f"UCe2eL{ch:03d}", 6.0 + (seed % 3), category=CATEGORIES6[ch % 6], seed=seed)
            )
    return clips


def build_dataset(settings: Settings, clips: list[SynthClip], n: int) -> pd.DataFrame:
    """Steps 1: clips.csv + caption store -> data build -> data sample -> audio placed on disk."""
    synth.write_world(clips, datasets_dir=Path(os.environ["CLIPBENCH_DATASETS_DIR"]), data_dir=settings.data_dir)
    invoke("data", "build", "--lang", LANG)
    invoke("data", "sample", "--lang", LANG, "--n", str(n))
    sample = pd.read_parquet(settings.sample_path)
    durations = {}
    for r in sample.itertuples(index=False):
        durations[r.video_id] = float(r.duration_s)
    synth.place_audio(settings.audio_dir, durations)
    return sample


# ---------------------------------------------------------------- references (as the review UI saves them)


def review(settings: Settings, plan: dict[str, str]) -> dict[str, str]:
    """Save verified files through clipbench.review.store, the code the review server calls.

    plan: clip id -> "verified" | "skipped" | "unusable" | "mismatch". "mismatch" is saved like a
    verified clip, then its WAV is replaced (a later re-fetch), so its audio_sha256 no longer matches.
    Returns clip id -> the text saved (the reviewer's correction of the creator caption)."""
    from clipbench.review import store

    queue = store.load_queue(settings)
    by_id = {it.video_id: it for it in queue.items}
    saved: dict[str, str] = {}
    for vid, kind in plan.items():
        item = by_id[vid]
        creator, _ = store.load_creator(vid, settings)
        words = creator.split()
        text = " ".join(["corregido", *words[1:]])  # the reviewer fixed the first word
        record = store.build_record(
            settings,
            item,
            text="" if kind == "skipped" else text,
            original_text=creator,
            tags=["unusable"] if kind == "unusable" else (["background_music"] if len(saved) % 3 == 0 else []),
            reviewer="e2e-test",
            skipped=kind == "skipped",
            category=item.category or "other",
        )
        store.save_verified(settings, record)
        saved[vid] = record["text"]
        if kind == "mismatch":
            hashes_path = settings.audio_dir / "hashes.json"
            hashes = json.loads(hashes_path.read_text())
            sha = synth.write_wav(settings.audio_dir / f"{vid}.wav", item.duration_s or 7.0, seed=99_999)
            hashes[vid] = {"original": sha, "wav": sha}
            hashes_path.write_text(json.dumps(hashes))
    return saved


# ---------------------------------------------------------------- fake providers


class SimulatedCrash(BaseException):
    """Not an Exception, so the runner's per job handler cannot catch it: the run dies mid way."""


def _frac(video_id: str) -> float:
    import hashlib

    return int(hashlib.sha256(video_id.encode()).hexdigest()[:6], 16) / 0xFFFFFF


class E2EFake(FakeProvider):
    """FakeProvider plus the behaviours the pipeline must cope with. Behaviour knobs are attributes, not
    options, so they never change the cache key."""

    id: ClassVar[str] = "e2e-base"
    bad_refs: ClassVar[set[str]] = set()  # clips whose reference is wrong: every provider "fails" them

    def __init__(self, *, skip_share: float = 0.0, crash_after: int | None = None, overbill: float = 1.0, **kw):
        super().__init__(**kw)
        self.skip_share, self.crash_after, self.overbill = skip_share, crash_after, overbill

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        assert clip is not None and Path(audio_path).is_file()
        if self.crash_after is not None and self.calls >= self.crash_after:
            raise SimulatedCrash(f"crash after {self.calls} calls")
        if self.skip_share and _frac(clip.video_id) < self.skip_share:
            self.calls += 1
            raise ProviderSkip("no output for this clip")
        res = await super().transcribe(audio_path, language, clip=clip)
        if clip.video_id in self.bad_refs:
            res.text = " ".join(["ruido"] * 12)
        res.cost_usd *= self.overbill
        return res


def fake(pid: str, *, paid: bool = False, **kw) -> E2EFake:
    cls = type(f"Fake_{pid.replace('-', '_')}", (E2EFake,), {"id": pid, "paid": paid})
    return cls(**kw)


def small_providers() -> list[E2EFake]:
    return [
        fake("fake-a", sub_rate=0.02, del_rate=0.01, ins_rate=0.01, seed=1),
        fake("fake-b", sub_rate=0.08, del_rate=0.03, ins_rate=0.02, seed=2),
        fake("fake-c", sub_rate=0.18, del_rate=0.05, ins_rate=0.05, seed=3),
        fake("fake-patchy", sub_rate=0.05, seed=4, skip_share=0.4),
        fake("fake-paid", paid=True, sub_rate=0.04, seed=5, cost_per_minute=0.5),
    ]


def go(providers, settings: Settings, run_id: str, **kw):
    kw.setdefault("stability", False)
    return asyncio.run(
        run(
            providers,
            "verified",
            run_id=run_id,
            settings=settings,
            language=LANG,
            progress=False,
            console=Console(file=open(os.devnull, "w")),  # noqa: SIM115 - closed with the process
            **kw,
        )
    )


# ---------------------------------------------------------------- scans


def published_files(settings: Settings) -> list[Path]:
    roots = [settings.results_dir, settings.site_dir, Path(os.environ["CLIPBENCH_WEB_DIR"]) / "public" / "data"]
    return sorted(p for r in roots if r.exists() for p in r.rglob("*") if p.is_file())


def file_strings(path: Path) -> list[str]:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
        return [str(v) for c in df.columns for v in df[c].dropna() if isinstance(v, str)]
    if path.suffix in {".png", ".jpg", ".webp"}:
        return []
    return [path.read_text(errors="replace")]


def ngrams(text: str, n: int = 9) -> set[tuple[str, ...]]:
    toks = re.findall(r"[a-záéíóúñü]+", text.lower())
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


# ---------------------------------------------------------------- 1 + 2: dataset and references

ODD = {"e2enocap001", "e2elong0001", "e2etiny0001", "e2etrans001"}


def small_plan(sample: pd.DataFrame) -> dict[str, str]:
    ids = list(sample.sort_values("sample_rank")["video_id"])
    plan = dict.fromkeys(ids[:20], "verified")
    plan |= dict.fromkeys(ids[20:22], "skipped")
    plan |= dict.fromkeys(ids[22:24], "unusable")
    plan[ids[24]] = "mismatch"
    return plan  # ids[25:] stay unreviewed: creator captions only


def test_dataset_and_references(world):
    from clipbench.data.manifest import COLUMNS
    from clipbench.data.references import load_clips, load_reference

    clips = small_world()
    sample = build_dataset(world, clips, n=60)

    # data build: one manifest row per clips.csv row, tracks classified from info.json
    m = pd.read_parquet(world.manifest_path).set_index("video_id")
    assert list(pd.read_parquet(world.manifest_path).columns) == COLUMNS
    assert len(m) == len(clips) and set(m["language"]) == {LANG}
    assert not m.loc["e2enocap001", "has_creator_track"] and m.loc["e2enocap001", "has_auto_track"]
    assert not m.loc["e2etrans001", "has_creator_track"]  # an auto translation is never a creator track
    assert m.loc["e2elong0001", "clip_source"] == "long"
    win = m.loc["e2ewindow01@60000-72000"]
    assert win["clip_source"] == "window" and win["duration_s"] == 12.0 and win["source_id"] == "e2ewindow01"
    assert "/tracks/windows/" in win["creator_track_path"]
    big = m[m["channel_id"] == "UCe2ebig"]
    assert len(big) == 10 and set(big["category"]) == {"cooking_tutorial"} and big["category_guess"].all()
    assert set(m.loc[m["channel_id"] == "UCe2ech00", "info_source"]) == {"clipbench"}

    # data sample: manifest columns + sample_rank, eligibility, 5% channel cap (floor(0.05 * 60) = 3)
    assert list(sample.columns) == COLUMNS + ["sample_rank"]
    assert not ODD & set(sample["video_id"])
    per_channel = sample["channel_id"].value_counts()
    assert per_channel.max() == 3 and per_channel["UCe2ebig"] == 3
    assert len(sample) == 8 * 3 + 3 + 1
    assert sorted(sample["sample_rank"]) == list(range(len(sample)))

    # data check: the sample and the pool both fail (9 or 10 channels, one channel at 10 of 35)
    res = invoke("data", "check", "--lang", LANG, ok=False)
    assert res.exit_code == 1 and "need at least 30" in res.output
    res = invoke("data", "check", "--lang", LANG, "--pool", ok=False)
    assert res.exit_code == 1 and "eligible pool: 35 clips" in res.output and "channel UCe2ebig is 28.6%" in res.output

    # audio is where fetch-audio leaves it
    hashes = json.loads((world.audio_dir / "hashes.json").read_text())
    assert set(hashes) == set(sample["video_id"])

    # references: verified files written by the review store
    plan = small_plan(sample)
    saved = review(world, plan)
    for vid, kind in plan.items():
        rec = json.loads((world.verified_dir / f"{vid}.json").read_text())
        assert rec["reviewer"] == "e2e-test" and rec["language"] == LANG and rec["skipped"] == (kind == "skipped")
        if kind != "mismatch":
            assert rec["audio_sha256"] == hashes[vid]["wav"]
    verified = {c.video_id: c for c in load_clips("verified", world, language=LANG)}
    want = {v for v, k in plan.items() if k == "verified"}
    assert set(verified) == want  # skipped, unusable, stale audio and unreviewed clips are all out
    for vid, c in verified.items():
        assert c.reference.tier == "verified" and c.reference.text == saved[vid]
        assert c.reference.text != load_reference(vid, "creator", world).text
        assert c.reference.timing_source == "creator" and c.reference.words  # word timings from the track
    unreviewed = set(sample["video_id"]) - set(plan)
    assert len(unreviewed) == 3
    for vid in unreviewed:  # creator captions never count as verified
        assert load_reference(vid, "verified", world) is None
        assert load_reference(vid, "creator", world).tier == "creator"
    creator = load_clips("creator", world, language=LANG)
    assert len(creator) == len(sample) and {c.reference.tier for c in creator} == {"creator"}

    # regression: the clip whose audio changed after review is not "verified" in the funnel or the check
    # (load_clips drops it; both used to count it)
    from clipbench.data.check import funnel

    f = funnel(sample, world.audio_dir, world.verified_dir, json.loads((world.audio_dir / "hashes.json").read_text()))
    assert f == {"sampled": 28, "audio_available": 28, "reviewed": 25, "skipped": 2, "unusable": 2, "verified": 20}
    res = invoke("data", "check", "--lang", LANG, "--verified", ok=False)
    assert res.exit_code == 1 and "verified clips of the sample: 20 clips" in res.output


def test_review_queue_reads_clips_csv_from_datasets_dir(world):
    """Regression: the review queue's category prefill read <repo>/datasets, not CLIPBENCH_DATASETS_DIR."""
    from clipbench.review import store

    build_dataset(world, small_world(), n=60)
    vid = "e2es0000xxx"
    for path in (world.sample_path, world.manifest_path):  # a stale sample and manifest with no category
        df = pd.read_parquet(path)
        df.loc[df["video_id"] == vid, "category"] = None
        df.to_parquet(path, index=False)
    item = next(it for it in store.load_queue(world).items if it.video_id == vid)
    assert item.category == "talking_head"  # from datasets/es/clips.csv in the tmp datasets dir


# ---------------------------------------------------------------- 3: the runner


def small_setup(world: Settings) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    sample = build_dataset(world, small_world(), n=60)
    plan = small_plan(sample)
    saved = review(world, plan)
    verified = sorted(v for v, k in plan.items() if k == "verified")
    E2EFake.bad_refs = {verified[0]}  # a wrong reference: every provider is far from it
    return sample, saved, verified


@pytest.fixture
def small(world):
    out = small_setup(world)
    yield out
    E2EFake.bad_refs = set()


def test_runner_cache_resume_budget(world, small):
    _, saved, verified = small
    n = len(verified)
    assert n == 20

    # -- a full run: five providers, stability reruns for the paid one
    provs = small_providers()
    res = go(provs, world, "e2e-small", stability=True, stability_n=3, stability_repeats=1)
    rows = pd.read_parquet(res.rows_path)
    assert list(rows.columns) == ROW_COLUMNS
    assert len(rows) == 5 * n + 3  # + 3 stability rows (repeat 1) for the paid provider
    assert set(rows.loc[rows["repeat"] == 1, "provider"]) == {"fake-paid"}
    assert set(rows["video_id"]) == set(verified) and set(rows["tier"]) == {"verified"}
    assert set(rows["language"]) == {LANG}
    patchy = rows[rows["provider"] == "fake-patchy"]
    n_skip = int((patchy["status"] == "skipped").sum())
    assert 0 < n_skip < n and set(patchy.loc[patchy["status"] == "skipped", "error"]) == {"no output for this clip"}
    assert (rows.loc[rows["provider"] != "fake-patchy", "status"] == "ok").all()
    assert not rows["cached"].any()
    assert res.n_calls == {"fake-a": n, "fake-b": n, "fake-c": n, "fake-patchy": n, "fake-paid": n + 3}

    texts = pd.read_parquet(res.texts_path)
    assert list(texts.columns) == TEXT_COLUMNS
    assert len(texts) == int((rows["status"] == "ok").sum())  # text only for ok rows
    refs = pd.read_parquet(res.refs_path)
    assert list(refs.columns) == REF_COLUMNS
    assert dict(zip(refs["video_id"], refs["ref_text"], strict=True)) == {v: saved[v] for v in verified}
    assert set(refs["tier"]) == {"verified"} and refs["words_json"].notna().all()
    assert res.rows_path.is_relative_to(world.results_dir) and res.texts_path.is_relative_to(world.data_dir)
    assert res.refs_path.is_relative_to(world.data_dir)  # text never lands under results/

    paid = rows[rows["provider"] == "fake-paid"]
    spend = load_spend(world.spend_path)
    assert spend["total_usd"] == pytest.approx(paid["cost_usd"].sum()) and spend["total_usd"] > 0
    assert set(spend["by_provider"]) == {"fake-paid"} and len(spend["events"]) == n + 3
    assert {e["run_id"] for e in spend["events"]} == {"e2e-small"}
    assert rows.loc[rows["provider"] != "fake-paid", "cost_usd"].sum() == 0

    meta = json.loads(res.meta_path.read_text())
    assert meta["run_id"] == "e2e-small" and meta["language"] == LANG and meta["tier"] == "verified"
    assert meta["n_clips"] == n and meta["kind"] == "batch" and meta["budget_stopped"] == []
    assert sorted(p["id"] for p in meta["providers"]) == sorted(p.id for p in provs)
    assert meta["spent_after_usd"] - meta["spent_before_usd"] == pytest.approx(spend["total_usd"])
    assert meta["stability_n"] == 3 and meta["stability_repeats"] == 1
    assert not (res.rows_path.parent / "rows.part.jsonl").exists()

    # -- rerun under a new id: every ok row is a cache hit, nothing is billed
    before = load_spend(world.spend_path)["total_usd"]
    again = go(small_providers(), world, "e2e-small-cached", stability=True, stability_n=3, stability_repeats=1)
    rows2 = pd.read_parquet(again.rows_path)
    ok2 = rows2[rows2["status"] == "ok"]
    assert ok2["cached"].all() and len(ok2) == int((rows["status"] == "ok").sum())
    # only the skipped clips go out again (a ProviderSkip is not a result, so it is not cached)
    assert again.n_calls == {"fake-a": 0, "fake-b": 0, "fake-c": 0, "fake-patchy": n_skip, "fake-paid": 0}
    assert load_spend(world.spend_path)["total_usd"] == before
    t1 = pd.read_parquet(res.texts_path).set_index(["provider", "video_id", "repeat"])["hyp_text"]
    t2 = pd.read_parquet(again.texts_path).set_index(["provider", "video_id", "repeat"])["hyp_text"]
    assert t1.sort_index().equals(t2.sort_index())

    # -- crash mid run, then resume the same run id (no cache, so only resume can save the calls)
    with pytest.raises(SimulatedCrash):
        go([fake("fake-a", sub_rate=0.02, seed=11, crash_after=7)], world, "e2e-crash", no_cache=True)
    part = world.runs_dir / "e2e-crash" / "rows.part.jsonl"
    assert not (world.runs_dir / "e2e-crash" / "rows.parquet").exists()
    assert len(part.read_text().splitlines()) == 7
    with part.open("a") as fh:
        fh.write('{"run_id": "e2e-crash", "provider": "fak')  # torn last line from the crash
    resumed = go([fake("fake-a", sub_rate=0.02, seed=11)], world, "e2e-crash", no_cache=True)
    assert resumed.n_calls == {"fake-a": n - 7}
    rows3 = pd.read_parquet(resumed.rows_path)
    assert len(rows3) == n and (rows3["status"] == "ok").all() and rows3["video_id"].is_unique
    assert len(pd.read_parquet(resumed.texts_path)) == n and not part.exists()

    # -- budget: refused at the start
    spent = load_spend(world.spend_path)["total_usd"]
    tight = replace(world, budget_usd=spent + 0.01)
    with pytest.raises(BudgetExceeded):
        go([fake("fake-pricey", paid=True, cost_per_minute=2.0, seed=12)], tight, "e2e-refused")
    assert not (world.runs_dir / "e2e-refused").exists()
    assert load_spend(world.spend_path)["total_usd"] == spent

    # -- budget: allowed at the start, stopped mid run (each call bills 3x its estimate)
    over = fake("fake-overbill", paid=True, cost_per_minute=0.6, seed=13, overbill=3.0)
    est_total = sum(over.estimate_cost(c) for c in rows3["audio_seconds"])
    max_est = max(over.estimate_cost(c) for c in rows3["audio_seconds"])
    cap = spent + est_total + 1e-9
    stopped = go([over], replace(world, budget_usd=cap), "e2e-budget")
    rows4 = pd.read_parquet(stopped.rows_path)
    n_ok, n_budget = int((rows4["status"] == "ok").sum()), int((rows4["error"] == BUDGET_ERROR).sum())
    assert n_ok > 0 and n_budget > 0 and n_ok + n_budget == n
    assert set(rows4.loc[rows4["status"] != "ok", "status"]) == {"skipped"}
    meta4 = json.loads(stopped.meta_path.read_text())
    assert meta4["budget_stopped"] == ["fake-overbill"]
    after = load_spend(world.spend_path)["total_usd"]
    # the guard reserves the estimate, so it can only overshoot by what in-flight calls billed above it
    assert spent < after <= cap + 2 * max_est
    # budget rows are retried on resume once the cap allows it
    resumed4 = go([fake("fake-overbill", paid=True, cost_per_minute=0.6, seed=13)], replace(world, budget_usd=100.0), "e2e-budget")
    assert resumed4.n_calls == {"fake-overbill": n_budget}
    assert (pd.read_parquet(resumed4.rows_path)["status"] == "ok").all()


# ---------------------------------------------------------------- 4 + 5: analysis, outputs and privacy


def web_data() -> Path:
    return Path(os.environ["CLIPBENCH_WEB_DIR"]) / "public" / "data"


def analyse(run_id: str) -> dict:
    invoke("score", run_id)
    invoke("errors", run_id)
    invoke("report", run_id)
    invoke("export", run_id)
    return json.loads((web_data() / "runs" / f"{run_id}.json").read_text())


def check_aggregates(world: Settings, run_id: str, providers: set[str]) -> pd.DataFrame:
    agg = pd.read_parquet(world.results_dir / run_id / "aggregates.parquet")
    head = agg[(agg["tier"] == "verified") & (agg["slice_kind"] == "all") & (agg["metric"] == "wer")]
    assert set(head["provider"]) == providers
    assert head[["error_rate", "ci_low", "ci_high"]].notna().all().all()
    assert ((head["ci_low"] <= head["error_rate"]) & (head["error_rate"] <= head["ci_high"])).all()
    return head


def test_small_run_is_scored_but_not_published(world, small):
    from clipbench.review import store

    sample, saved, verified = small
    go(small_providers(), world, "e2e-small", stability=True, stability_n=3, stability_repeats=1)
    run_json = analyse("e2e-small")
    res_dir = world.results_dir / "e2e-small"

    # score: aggregates with intervals for every provider; paired tests exist in the raw output
    head = check_aggregates(world, "e2e-small", {"fake-a", "fake-b", "fake-c", "fake-patchy", "fake-paid"})
    rates = head.set_index("provider")["error_rate"]
    assert rates["fake-a"] < rates["fake-b"] < rates["fake-c"]  # error rates follow the injected rates
    raw_paired = pd.read_parquet(res_dir / "paired.parquet")
    assert "fake-patchy" in set(raw_paired["provider_a"]) | set(raw_paired["provider_b"])
    assert len(pd.read_parquet(res_dir / "stability.parquet")) > 0
    scores = pd.read_parquet(res_dir / "scores.parquet")
    assert set(scores["video_id"]) == set(verified) and set(scores["tier"]) == {"verified"}

    # gate: excluded low coverage provider with a reason; unpublished below every threshold
    (lang,) = run_json["languages"]
    eligible = ["fake-a", "fake-b", "fake-c", "fake-paid"]
    assert lang["lang"] == LANG and lang["providers_eligible"] == eligible and lang["providers_ok"] == 4
    (excl,) = lang["providers_excluded"]
    assert excl["provider"] == "fake-patchy" and excl["coverage"] < 0.9
    assert "below the 90.0% minimum" in excl["reason"] and excl["n_skipped"] > 0
    assert lang["verified_clips"] == 20 and lang["published"] is False
    assert lang["gate"] == {"verified_clips_ok": False, "providers_ok": False, "diversity_ok": False}
    assert lang["verified_diversity"]["problems"]
    assert run_json["ranking"] == {}
    for p in run_json["paired"]:  # paired comparisons only among eligible providers
        assert p["provider_a"] in eligible and p["provider_b"] in eligible
    assert run_json["paired"] and {"verdict", "verdict_raw", "p_adjusted"} <= set(run_json["paired"][0])
    elig = {a["provider"]: a["eligible"] for a in run_json["aggregates"]}
    assert elig["fake-patchy"] is False and all(elig[p] for p in eligible)
    findings = json.loads((web_data() / "findings" / f"{LANG}.json").read_text())
    assert findings["published"] is False and findings["findings"] == []
    index = json.loads((web_data() / "index.json").read_text())
    assert index["latest"] == "e2e-small" and not index.get("demo")
    assert next(x for x in index["languages"] if x["lang"] == LANG)["published"] is False

    # errors: errors.json, and the clip with a wrong reference goes to rereview.json and the review queue
    errors = json.loads((res_dir / "errors.json").read_text())
    assert set(errors["providers"]) >= set(eligible)
    assert errors["all_fail"] == [verified[0]]
    assert json.loads((world.data_dir / "rereview.json").read_text()) == [verified[0]]
    queue = store.load_queue(world)
    assert queue.items[0].video_id == verified[0] and queue.items[0].flagged
    for ex in errors["examples"]:
        assert len(ex["ref_snippet"].split()) <= 4 and len(ex["hyp_snippet"].split()) <= 4

    # report and site
    report = (res_dir / "report.md").read_text()
    assert "fake-a" in report and (world.site_dir / "index.html").is_file()

    # privacy: the generated outputs pass, then each planted leak fails
    res = invoke("publish", "--check")
    assert "publish check passed" in res.output
    leak_scan(world, saved)

    wav = world.site_dir / "planted.wav"
    shutil.copy(world.audio_dir / f"{verified[1]}.wav", wav)
    res = invoke("publish", "--check", ok=False)
    assert res.exit_code == 1 and "planted.wav: audio/video file extension .wav" in res.output
    disguised = world.results_dir / "e2e-small" / "chart.png"
    wav.rename(disguised)  # a WAV with an innocent name is caught by its RIFF header
    res = invoke("publish", "--check", ok=False)
    assert res.exit_code == 1 and "audio magic bytes RIFF" in res.output
    disguised.unlink()

    words = saved[verified[2]].split()
    planted = web_data() / "planted.json"
    planted.write_text(json.dumps({"note": " ".join(words[2:10])}))  # 8 words: allowed
    invoke("publish", "--check")
    planted.write_text(json.dumps({"note": " ".join(words[2:11])}))  # 9 words: a leak
    res = invoke("publish", "--check", ok=False)
    assert res.exit_code == 1 and "planted.json: contains more than 8 consecutive reference words" in res.output
    planted.unlink()
    md = world.results_dir / "e2e-small" / "notes.md"
    md.write_text("Quote: " + " ".join(words[:9]).upper() + ".")  # case and punctuation do not hide it
    res = invoke("publish", "--check", ok=False)
    assert res.exit_code == 1 and "notes.md" in res.output
    md.unlink()
    invoke("publish", "--check")


def leak_scan(world: Settings, saved: dict[str, str]) -> None:
    """Independent of clipbench.report.publish: no 9 word window of any reference or creator caption and
    no absolute local path in anything under results/, site/ or the web data."""
    from clipbench.data.references import load_reference

    refs = set()
    for vid, text in saved.items():
        refs |= ngrams(text)
        creator = load_reference(vid, "creator", world)
        if creator:
            refs |= ngrams(creator.text)
    files = published_files(world)
    assert any(p.suffix == ".json" for p in files) and any(p.suffix == ".parquet" for p in files)
    local = {str(world.data_dir.parent), str(world.data_dir.parent.resolve()), str(Path.home()), str(Path.cwd())}
    for path in files:
        for s in file_strings(path):
            hit = next((" ".join(g) for g in ngrams(s) if g in refs), None)
            assert hit is None, f"{path} holds reference text: {hit}"
            for loc in local:
                assert loc not in s, f"{path} holds a local path ({loc})"


# ---------------------------------------------------------------- 4b: a set that meets the gate


def test_large_run_meets_gate_and_publishes(world):
    sample = build_dataset(world, large_world(), n=150)
    assert len(sample) == 120 and sample["channel_id"].value_counts().max() == 3
    invoke("data", "check", "--lang", LANG)
    res = invoke("data", "check", "--lang", LANG, "--pool")
    assert "OK" in res.output
    saved = review(world, dict.fromkeys(sample["video_id"], "verified"))
    res = invoke("data", "check", "--lang", LANG, "--verified")
    assert "verified clips of the sample: 120 clips, 40 channels" in res.output

    rates = [0.01, 0.03, 0.06, 0.10, 0.15, 0.22]
    provs = [fake(f"fake-{i}", sub_rate=r, del_rate=r / 3, ins_rate=r / 3, seed=20 + i) for i, r in enumerate(rates)]
    provs.append(fake("fake-patchy", sub_rate=0.0, del_rate=0.0, ins_rate=0.0, seed=30, skip_share=0.3))
    go(provs, world, "e2e-large")
    run_json = analyse("e2e-large")

    eligible = [f"fake-{i}" for i in range(6)]
    check_aggregates(world, "e2e-large", set(eligible) | {"fake-patchy"})
    (lang,) = run_json["languages"]
    assert lang["verified_clips"] == 120 and lang["providers_eligible"] == eligible and lang["providers_ok"] == 6
    assert [e["provider"] for e in lang["providers_excluded"]] == ["fake-patchy"]
    div = lang["verified_diversity"]
    assert div["passes"] and div["channels"] == 40 and div["categories"] == sorted(CATEGORIES6) and not div["problems"]
    assert lang["gate"] == {"verified_clips_ok": True, "providers_ok": True, "diversity_ok": True}
    assert lang["published"] is True

    # the ranking: eligible providers only, by error rate, 1-based; the near perfect but patchy one is out
    ranking = run_json["ranking"]["verified"]["wer"]
    assert [r["provider"] for r in ranking] == eligible
    assert [r["rank"] for r in ranking] == [1, 2, 3, 4, 5, 6]
    assert all(r["n_clips"] == 120 and r["ci_low"] <= r["error_rate"] <= r["ci_high"] for r in ranking)
    for p in run_json["paired"]:
        assert p["provider_a"] in eligible and p["provider_b"] in eligible
    assert len({(p["provider_a"], p["provider_b"]) for p in run_json["paired"] if p["metric"] == "wer"
                and p["slice_kind"] == "all" and p["tier"] == "verified"}) == 15  # every eligible pair, once
    findings = json.loads((web_data() / "findings" / f"{LANG}.json").read_text())
    assert findings["published"] is True and findings["findings"]
    assert not any("fake-patchy" in json.dumps(f) for f in findings["findings"])
    index = json.loads((web_data() / "index.json").read_text())
    assert next(x for x in index["languages"] if x["lang"] == LANG)["published"] is True
    assert (world.site_dir / "index.html").is_file()

    invoke("publish", "--check")
    leak_scan(world, saved)
