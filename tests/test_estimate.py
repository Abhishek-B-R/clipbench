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


def test_english_only_model_is_not_priced_on_other_languages(tmp_settings, monkeypatch):
    """cf-whisper-tiny-en supports only English; the full-run estimate must not price it on ja/ko/es/hi."""
    from clipbench import budget
    from clipbench.providers import registry

    tiny = registry.make("cf-whisper-tiny-en")
    assert budget.supports_language(tiny, "en") and not budget.supports_language(tiny, "ja")
    real = dict((t, u) for t, _, u in estimate_all(tmp_settings, Assumptions(clips_per_lang=10)))
    monkeypatch.setattr(budget, "supports_language", lambda p, lang: True)
    every = dict((t, u) for t, _, u in estimate_all(tmp_settings, Assumptions(clips_per_lang=10)))
    assert real["batch"] < every["batch"]
