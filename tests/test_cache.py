from __future__ import annotations

from typing import ClassVar

import pytest

from clipbench.budget import repeat_settings_hash
from clipbench.models import Result
from clipbench.providers.cache import ResultCache
from clipbench.providers.fake import FakeProvider
from clipbench.runner import run
from tests.fixtures.make_clips import make_clips


class PaidFake(FakeProvider):
    id: ClassVar[str] = "fake"
    paid: ClassVar[bool] = True


@pytest.fixture
def clips(tmp_path):
    return make_clips(tmp_path / "audio")


def test_cache_roundtrip(tmp_path):
    cache = ResultCache(tmp_path / "cache")
    r = Result(text="こんにちは", latency_ms=12.0, audio_seconds=3.0, cost_usd=0.01, model_id="m/x")
    assert cache.get("p", "m/x", "abc", "h") is None
    cache.put("p", "m/x", "abc", "h", r)
    assert cache.get("p", "m/x", "abc", "h") == r
    assert cache.get("p", "m/x", "abc", "other") is None


def test_repeat_hash_differs_per_repeat():
    p = FakeProvider()
    hashes = {repeat_settings_hash(p, r) for r in range(4)}
    assert len(hashes) == 4
    assert repeat_settings_hash(p, 0) == p.settings_hash()
    assert FakeProvider(seed=1).settings_hash() != p.settings_hash()


async def test_second_run_hits_cache(tmp_settings, clips):
    p1 = FakeProvider()
    r1 = await run([p1], "verified", clips=clips, settings=tmp_settings, progress=False)
    assert p1.calls == len(clips)
    assert not r1.rows["cached"].any()

    p2 = FakeProvider()
    r2 = await run([p2], "verified", clips=clips, settings=tmp_settings, progress=False)
    assert p2.calls == 0
    assert r2.rows["cached"].all()
    assert (r2.rows["status"] == "ok").all()
    # A cache hit keeps the original request latency, not ~0.
    assert (r2.rows["latency_ms"] == 50.0).all()
    # Same hypotheses and numbers from the cache.
    assert list(r1.rows["hyp_chars"]) == list(r2.rows["hyp_chars"])


async def test_no_cache_forces_calls(tmp_settings, clips):
    await run([FakeProvider()], "verified", clips=clips, settings=tmp_settings, progress=False)
    p = FakeProvider()
    r = await run([p], "verified", clips=clips, settings=tmp_settings, progress=False, no_cache=True)
    assert p.calls == len(clips)
    assert not r.rows["cached"].any()


async def test_changed_settings_miss_the_cache(tmp_settings, clips):
    await run([FakeProvider(seed=0)], "verified", clips=clips, settings=tmp_settings, progress=False)
    p = FakeProvider(seed=1)
    await run([p], "verified", clips=clips, settings=tmp_settings, progress=False)
    assert p.calls == len(clips)


async def test_stability_reruns_bypass_main_cache_but_are_cached_themselves(tmp_settings, clips):
    p1 = PaidFake(cost_per_minute=0.06)
    r1 = await run(
        [p1], "verified", clips=clips, settings=tmp_settings, progress=False, stability_n=2, stability_repeats=3
    )
    assert p1.calls == len(clips) + 2 * 3
    reps = r1.rows[r1.rows["repeat"] > 0]
    assert len(reps) == 6 and set(reps["repeat"]) == {1, 2, 3}
    assert reps["video_id"].nunique() == 2

    p2 = PaidFake(cost_per_minute=0.06)
    r2 = await run(
        [p2], "verified", clips=clips, settings=tmp_settings, progress=False, stability_n=2, stability_repeats=3
    )
    assert p2.calls == 0
    assert r2.rows["cached"].all()
