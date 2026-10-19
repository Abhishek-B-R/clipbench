from __future__ import annotations

import pytest

from clipbench.config import get_settings


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Settings with data/, results/ and site/ pointed at a tmp dir."""
    monkeypatch.setenv("CLIPBENCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIPBENCH_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("CLIPBENCH_SITE_DIR", str(tmp_path / "site"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
