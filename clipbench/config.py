"""Project settings and well known paths.

Settings come from clipbench.toml at the repo root, overridden by environment variables.
All paths are resolved against the repo root so commands work from any directory.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(os.environ.get("CLIPBENCH_ROOT", Path(__file__).resolve().parent.parent))

load_dotenv(REPO_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    root: Path
    language: str
    vako_root: Path
    budget_usd: float
    sample_seed: int

    # Creator content lives under data/ (gitignored).
    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("CLIPBENCH_DATA_DIR", self.root / "data"))

    @property
    def results_dir(self) -> Path:
        return Path(os.environ.get("CLIPBENCH_RESULTS_DIR", self.root / "results"))

    @property
    def site_dir(self) -> Path:
        return Path(os.environ.get("CLIPBENCH_SITE_DIR", self.root / "site"))

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.parquet"

    @property
    def sample_path(self) -> Path:
        return self.data_dir / "sample.parquet"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def info_dir(self) -> Path:
        """info.json files clipbench fetched itself, for tracks Vako has no metadata for."""
        return self.data_dir / "info"

    @property
    def verified_dir(self) -> Path:
        return self.data_dir / "verified"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def spend_path(self) -> Path:
        return self.data_dir / "spend.json"

    @property
    def runs_text_dir(self) -> Path:
        """Per run hypothesis text and reference snapshots. Never published."""
        return self.data_dir / "runs"

    @property
    def runs_dir(self) -> Path:
        """Per run numeric rows (no caption text)."""
        return self.results_dir / "runs"

    @property
    def vako_src(self) -> Path:
        return self.vako_root / ".vr" / "src" / self.language


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    path = REPO_ROOT / "clipbench.toml"
    raw: dict = tomllib.loads(path.read_text()) if path.exists() else {}
    return Settings(
        root=REPO_ROOT,
        language=os.environ.get("CLIPBENCH_LANGUAGE", raw.get("language", "ja")),
        vako_root=Path(os.environ.get("CLIPBENCH_VAKO_ROOT", raw.get("vako_root", "../Vako/Vako"))).expanduser(),
        budget_usd=float(os.environ.get("CLIPBENCH_BUDGET_USD", raw.get("budget_usd", 10.0))),
        sample_seed=int(raw.get("sample_seed", 7)),
    )
