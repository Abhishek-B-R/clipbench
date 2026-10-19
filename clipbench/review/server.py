"""FastAPI app for the review UI. Bind to 127.0.0.1 only (see clipbench.review.cli)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from clipbench.config import Settings, get_settings
from clipbench.models import TAGS
from clipbench.review import store

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SaveBody(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)


def create_app(
    settings: Settings | None = None,
    reviewer: str = "anonymous",
    only_rereview: bool = False,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="clipbench review", docs_url=None, redoc_url=None, openapi_url=None)
    queue = store.load_queue(settings, only_rereview=only_rereview)
    creator_cache: dict[str, tuple[str, list[dict] | None, str | None]] = {}

    def item_or_404(video_id: str) -> store.QueueItem:
        if not store.VIDEO_ID_RE.match(video_id) or video_id not in queue.by_id:
            raise HTTPException(status_code=404, detail="unknown video id")
        return queue.by_id[video_id]

    def creator(video_id: str) -> tuple[str, list[dict] | None, str | None]:
        if video_id not in creator_cache:
            try:
                text, words = store.load_creator(video_id, settings)
                creator_cache[video_id] = (text, words, None if text else "no creator caption found")
            except Exception as exc:  # show the problem in the UI instead of a 500
                return "", None, f"could not load creator caption: {exc}"
        return creator_cache[video_id]

    def queue_state() -> dict:
        statuses = [store.clip_status(settings, queue, it) for it in queue.items]
        done = sum(s in ("reviewed", "skipped") for s in statuses)
        resume = next((i for i, s in enumerate(statuses) if s in ("todo", "rereview")), None)
        return {
            "reviewer": reviewer,
            "language": settings.language,
            "only_rereview": only_rereview,
            "tags": TAGS,
            "total": len(queue.items),
            "done": done,
            "reviewed": statuses.count("reviewed"),
            "skipped": statuses.count("skipped"),
            "resume_index": resume,
            "items": [
                {
                    "video_id": it.video_id,
                    "status": s,
                    "flagged": it.flagged,
                    "channel": it.channel,
                    "duration_s": it.duration_s,
                }
                for it, s in zip(queue.items, statuses, strict=True)
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/queue")
    def get_queue() -> dict:
        return queue_state()

    @app.get("/api/clip/{video_id}")
    def get_clip(video_id: str) -> dict:
        item = item_or_404(video_id)
        original, words, error = creator(video_id)
        rec = store.read_verified(settings, video_id)
        saved = rec is not None and not rec.get("skipped")
        return {
            "video_id": video_id,
            "index": queue.index_of(video_id),
            "language": item.language,
            "channel": item.channel,
            "duration_s": item.duration_s,
            "flagged": item.flagged,
            "status": store.clip_status(settings, queue, item),
            "youtube_url": f"https://www.youtube.com/shorts/{video_id}",
            "audio_available": store.audio_path(settings, video_id).exists(),
            "original_text": original,
            "words": words,
            "error": error,
            "text": rec["text"] if saved else original,
            "tags": rec.get("tags", []) if rec else [],
            "saved": rec,
        }

    def save(item: store.QueueItem, text: str, tags: list[str], skipped: bool) -> dict:
        original, _, _ = creator(item.video_id)
        try:
            record = store.build_record(
                settings,
                item,
                text=text,
                original_text=original,
                tags=tags,
                reviewer=reviewer,
                skipped=skipped,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_verified(settings, record)
        return {"record": record, "queue": queue_state()}

    @app.post("/api/clip/{video_id}")
    def post_clip(video_id: str, body: SaveBody) -> dict:
        item = item_or_404(video_id)
        return save(item, body.text, body.tags, skipped=False)

    @app.post("/api/clip/{video_id}/skip")
    def skip_clip(video_id: str) -> dict:
        item = item_or_404(video_id)
        return save(item, "", [], skipped=True)

    @app.get("/audio/{video_id}")
    def audio(video_id: str) -> FileResponse:
        item_or_404(video_id)
        path = store.audio_path(settings, video_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no audio for this clip")
        return FileResponse(path, media_type="audio/wav")

    return app
