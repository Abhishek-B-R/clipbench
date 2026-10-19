"""YouTube's own automatic speech recognition track, read from disk.

This is a system under test, never a reference: it is scored against the creator or verified text like
any other provider. No network, no cost. The track comes from the manifest row
(`data/manifest.parquet`, column `auto_track_path`); clips with no auto track raise ProviderSkip.

Text: `parse_json3_text(..., strip_sound_tags=True)` so tags like [音楽] / [Music] are not counted as
words. Words: `parse_json3_words` (word timings when YouTube timed the track by word).
`latency_ms` is 0: the track already exists, so there is no request to time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from clipbench.config import get_settings
from clipbench.models import Clip, Result
from clipbench.providers.base import Provider, ProviderSkip

_MANIFEST_CACHE: dict[tuple[str, float], dict[str, dict[str, Any]]] = {}


def _manifest_rows(path: Path) -> dict[str, dict[str, Any]]:
    import pandas as pd

    key = (str(path), path.stat().st_mtime)
    rows = _MANIFEST_CACHE.get(key)
    if rows is None:
        df = pd.read_parquet(path)
        df = df.astype(object).where(df.notna(), None)
        rows = {str(r["video_id"]): r for r in df.to_dict("records")}
        _MANIFEST_CACHE.clear()
        _MANIFEST_CACHE[key] = rows
    return rows


class YouTubeAuto(Provider):
    id: ClassVar[str] = "youtube-auto"
    concurrency: ClassVar[int] = 8
    timeout_s: ClassVar[float] = 30.0
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False

    @classmethod
    def default_model_id(cls) -> str:
        return "youtube-asr"

    def price_per_minute(self) -> float:
        return 0.0

    def estimate_cost(self, audio_seconds: float) -> float:
        return 0.0

    def availability(self) -> tuple[bool, str]:
        path = get_settings().manifest_path
        if not path.exists():
            return False, f"{self.id}: skipped, no manifest at {path} (run `clipbench data build`)"
        return True, "ok"

    def _resolve(self, raw: str) -> Path | None:
        s = get_settings()
        p = Path(raw).expanduser()
        candidates = [p] if p.is_absolute() else [s.vako_root / p, s.root / p, s.data_dir / p, p]
        for c in candidates:
            if c.exists():
                return c
        return None

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is None:
            raise ProviderSkip("youtube-auto needs the clip to find its auto track")
        from clipbench.data.json3 import parse_json3_text, parse_json3_words  # lazy

        settings = get_settings()
        if not settings.manifest_path.exists():
            raise ProviderSkip("no manifest")
        row = _manifest_rows(settings.manifest_path).get(clip.video_id)
        if row is None:
            raise ProviderSkip("video not in the manifest")
        raw_path = row.get("auto_track_path")
        if not raw_path or row.get("has_auto_track") is False:
            raise ProviderSkip("no auto track")
        path = self._resolve(str(raw_path))
        if path is None:
            raise ProviderSkip(f"auto track missing on disk: {raw_path}")
        body = path.read_text(encoding="utf-8")
        text = parse_json3_text(body, language, strip_sound_tags=True)
        words = parse_json3_words(body)
        return Result(
            text=text,
            words=words or None,
            latency_ms=0.0,
            audio_seconds=clip.duration_s,
            cost_usd=0.0,
            raw_response={"track_key": row.get("auto_track_key"), "path": str(path)},
            model_id=self.model_id,
            provider_version=str(row.get("auto_track_key") or "auto"),
        )
