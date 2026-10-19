"""Wayin transcripts that Vako already paid for, read back. Never calls Wayin.

What exists (read only investigation of Vako, 2026-10-19):

- Vako's caption pipeline (`apps/api/src/captions/wayin.ts`, `run.ts`) sends a video URL to Wayin and
  stores the result in Postgres only: `Transcript.source = 'wayin'` (the other values are
  `youtube-creator` and `youtube-auto`), with the lines in `TranscriptSegment` (`index`, `startMs`,
  `endMs`, `text`). The video is `Video` with `provider = 'youtube'` and `sourceId` = the YouTube id.
- Nothing on disk under `.vr/` holds Wayin output: `.vr/cues/<lang>/<videoId>.json` only carries
  `"track": "creator"` or `"auto"` cues, and `.vr/clips`, `.vr/work` are curation batches. So there is no
  file route into Vako.
- A Vako row can be a slice of the video (`clipStartMs` / `clipEndMs`, transcript rebased to the
  slice). Only rows covering the whole video (`clipStartMs = 0` and `clipEndMs IS NULL`) are comparable
  with clipbench's full-length audio; slices are skipped.

Two routes, both optional:

1. Files: `data/wayin/<videoId>.json` (clipbench side, never published), either
   `{"text": "...", "words": [{"text", "start_ms", "end_ms"}]}` or Vako's cue shape
   `{"cues": [{"startMs", "endMs", "text"}]}` / `{"segments": [...]}`.
2. Database: only when `CLIPBENCH_WAYIN_DB=1` and `DATABASE_URL` are set and `psycopg` is installed.
   The connection is opened read only (`default_transaction_read_only=on`) and runs one SELECT.

Cost is 0 here: the money was spent by Vako, not by this benchmark.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, ClassVar

from clipbench.config import get_settings
from clipbench.models import Clip, Result, Word
from clipbench.providers.base import Provider, ProviderSkip

UNSPACED = {"ja", "zh", "th"}

DB_QUERY = """
SELECT s."startMs", s."endMs", s."text"
FROM "Video" v
JOIN "Transcript" t ON t."videoId" = v."id"
JOIN "TranscriptSegment" s ON s."transcriptId" = t."id"
WHERE v."provider" = 'youtube'
  AND v."sourceId" = %s
  AND t."source" = 'wayin'
  AND t."language" = %s
  AND v."clipStartMs" = 0
  AND v."clipEndMs" IS NULL
ORDER BY s."index"
"""


def _db_enabled() -> bool:
    return os.environ.get("CLIPBENCH_WAYIN_DB") == "1" and bool(os.environ.get("DATABASE_URL"))


def _psycopg_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("psycopg") is not None


def _join(parts: list[str], language: str) -> str:
    parts = [p.strip() for p in parts if p and p.strip()]
    return ("" if language in UNSPACED else " ").join(parts)


def parse_export(data: dict[str, Any], language: str) -> tuple[str, list[Word] | None]:
    """Text and optional line timings from a `data/wayin/<videoId>.json` export."""
    if isinstance(data.get("text"), str):
        words = None
        if isinstance(data.get("words"), list):
            words = [Word(**w) for w in data["words"]]
        return data["text"].strip(), words
    lines = data.get("cues") or data.get("segments") or []
    if not isinstance(lines, list):
        raise ValueError("unrecognised wayin export")
    text = _join([str(c.get("text", "")) for c in lines if isinstance(c, dict)], language)
    return text, None


class Wayin(Provider):
    id: ClassVar[str] = "wayin"
    concurrency: ClassVar[int] = 2
    timeout_s: ClassVar[float] = 60.0
    paid: ClassVar[bool] = False
    uploads_audio: ClassVar[bool] = False

    @classmethod
    def default_model_id(cls) -> str:
        return "wayin-vako"

    def price_per_minute(self) -> float:
        return 0.0

    def estimate_cost(self, audio_seconds: float) -> float:
        return 0.0

    @property
    def export_dir(self) -> Path:
        return get_settings().data_dir / "wayin"

    def availability(self) -> tuple[bool, str]:
        d = self.export_dir
        if d.is_dir() and any(d.glob("*.json")):
            return True, "ok"
        if _db_enabled():
            if not _psycopg_installed():
                return False, f"{self.id}: skipped, CLIPBENCH_WAYIN_DB=1 but psycopg is not installed"
            return True, "ok"
        return False, (
            f"{self.id}: skipped, no Wayin transcripts: Vako keeps them only in its database "
            f"(Transcript.source='wayin'); put exports in {d}/<videoId>.json or set CLIPBENCH_WAYIN_DB=1 "
            "with DATABASE_URL for a read only lookup"
        )

    def _from_file(self, video_id: str, language: str) -> tuple[str, list[Word] | None] | None:
        path = self.export_dir / f"{video_id}.json"
        if not path.exists():
            return None
        return parse_export(json.loads(path.read_text(encoding="utf-8")), language)

    def _from_db(self, video_id: str, language: str) -> tuple[str, list[Word] | None] | None:
        import psycopg  # optional, lazy

        with psycopg.connect(
            os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on", autocommit=False
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(DB_QUERY, (video_id, language))
                rows = cur.fetchall()
            conn.rollback()
        if not rows:
            return None
        return _join([r[2] for r in rows], language), None

    async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
        if clip is None:
            raise ProviderSkip("wayin needs the clip to find its transcript")
        found = self._from_file(clip.video_id, language)
        source = "file"
        if found is None and _db_enabled() and _psycopg_installed():
            found = await asyncio.to_thread(self._from_db, clip.video_id, language)
            source = "vako-db"
        if found is None:
            raise ProviderSkip("no wayin output for this clip")
        text, words = found
        if not text:
            raise ProviderSkip("wayin output is empty")
        return Result(
            text=text,
            words=words,
            latency_ms=0.0,
            audio_seconds=clip.duration_s,
            cost_usd=0.0,
            raw_response={"source": source},
            model_id=self.model_id,
            provider_version=source,
        )
