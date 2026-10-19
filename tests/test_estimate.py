from clipbench.estimate import Assumptions, estimate_all


def test_estimate_all_prices_every_track_without_keys(tmp_settings, monkeypatch):
    for key in ("DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    rows = estimate_all(tmp_settings, Assumptions(clips_per_lang=10))
    tracks = [t for t, _, _ in rows]
    assert tracks[:2] == ["batch", "language ID pass"]
    assert {"streaming", "SNR sweeps", "long-form", "semantic judge", "canaries, per month"} <= set(tracks)
    assert all(usd >= 0 for _, _, usd in rows)
    batch = dict((t, u) for t, _, u in rows)["batch"]
    assert batch > 0  # paid providers are priced even without keys


def test_estimate_scales_with_clips(tmp_settings):
    small = dict((t, u) for t, _, u in estimate_all(tmp_settings, Assumptions(clips_per_lang=10)))
    big = dict((t, u) for t, _, u in estimate_all(tmp_settings, Assumptions(clips_per_lang=20)))
    assert big["keyterms pass"] > small["keyterms pass"]
