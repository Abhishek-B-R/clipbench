"""FastAPI app for the review UI. Bind to 127.0.0.1 only (see clipbench.review.cli)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from clipbench.config import Settings, get_settings
from clipbench.models import TAGS
from clipbench.review import store

STATIC_DIR = Path(__file__).resolve().parent / "static"


class NonspeechSpan(BaseModel):
    start_ms: float
    end_ms: float
    kind: str


class CodeswitchSpan(BaseModel):
    start: int
    end: int
    lang: str = "en"
    text: str | None = None  # the tagged text, used to re-anchor if the offsets went stale


class SaveBody(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    nonspeech_spans: list[NonspeechSpan] = Field(default_factory=list)
    codeswitch_spans: list[CodeswitchSpan] = Field(default_factory=list)
    punctuated: bool = False


def create_app(
    settings: Settings | None = None,
    reviewer: str = "anonymous",
    only_rereview: bool = False,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="clipbench review", docs_url=None, redoc_url=None, openapi_url=None)
    queue = store.load_queue(settings, only_rereview=only_rereview)
    creator_cache: dict[str, tuple[str, list[dict] | None, str | None]] = {}
    peaks_cache: dict[tuple[str, int], dict] = {}

    def item_or_404(video_id: str) -> store.QueueItem:
        if not store.VIDEO_ID_RE.match(video_id) or video_id not in queue.by_id:
            raise HTTPException(status_code=404, detail="unknown clip id")
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
            "categories": store.CATEGORIES,
            "nonspeech_kinds": store.NONSPEECH_KINDS,
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
                    "category": it.category,
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
        text = rec["text"] if saved else original
        spans = []
        if saved:
            for sp in rec.get("codeswitch_spans") or []:
                try:
                    spans.append({**sp, "text": text[int(sp["start"]) : int(sp["end"])]})
                except (KeyError, TypeError, ValueError):
                    continue
        saved_category = rec.get("category") if saved else None
        return {
            "video_id": video_id,
            "index": queue.index_of(video_id),
            "language": item.language,
            "channel": item.channel,
            "duration_s": item.duration_s,
            "flagged": item.flagged,
            "status": store.clip_status(settings, queue, item),
            "kind": item.kind,
            "source_id": item.source_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "youtube_url": item.youtube_url,
            "audio_available": store.audio_path(settings, video_id).exists(),
            "original_text": original,
            "words": words,
            "error": error,
            "text": text,
            "tags": rec.get("tags", []) if saved else [],
            "category": saved_category or item.category,
            "category_prefilled": saved_category is None and item.category is not None,
            "nonspeech_spans": (rec.get("nonspeech_spans") or []) if saved else [],
            "codeswitch_spans": spans,
            "punctuated": bool(rec.get("punctuated")) if saved else False,
            "saved": rec,
        }

    def wav_duration_ms(video_id: str) -> int | None:
        path = store.audio_path(settings, video_id)
        if not path.exists():
            return None
        try:
            import soundfile

            return int(round(soundfile.info(str(path)).duration * 1000))
        except Exception:  # unreadable WAV: no clipping
            return None

    def save(item: store.QueueItem, body: SaveBody | None) -> dict:
        original, _, _ = creator(item.video_id)
        dropped: list[dict] = []
        try:
            if body is None:  # skip
                record = store.build_record(
                    settings, item, text="", original_text=original, tags=[], reviewer=reviewer, skipped=True
                )
            else:
                nonspeech = store.clean_nonspeech(
                    [s.model_dump() for s in body.nonspeech_spans], wav_duration_ms(item.video_id)
                )
                codeswitch, dropped = store.clean_codeswitch(
                    body.text, [s.model_dump() for s in body.codeswitch_spans]
                )
                record = store.build_record(
                    settings,
                    item,
                    text=body.text,
                    original_text=original,
                    tags=body.tags,
                    reviewer=reviewer,
                    skipped=False,
                    category=body.category,
                    nonspeech_spans=nonspeech,
                    codeswitch_spans=codeswitch,
                    punctuated=body.punctuated,
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_verified(settings, record)
        return {"record": record, "dropped_codeswitch_spans": dropped, "queue": queue_state()}

    @app.post("/api/clip/{video_id}")
    def post_clip(video_id: str, body: SaveBody) -> dict:
        return save(item_or_404(video_id), body)

    @app.post("/api/clip/{video_id}/skip")
    def skip_clip(video_id: str) -> dict:
        return save(item_or_404(video_id), None)

    @app.get("/api/clip/{video_id}/peaks")
    def peaks(video_id: str, n: int = Query(1000, ge=50, le=4000)) -> dict:
        item_or_404(video_id)
        path = store.audio_path(settings, video_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no audio for this clip")
        key = (video_id, n)
        if key not in peaks_cache:
            try:
                peaks_cache[key] = store.waveform_peaks(path, n)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"unreadable audio: {exc}") from exc
        return peaks_cache[key]

    @app.get("/audio/{video_id}")
    def audio(video_id: str) -> FileResponse:
        item_or_404(video_id)
        path = store.audio_path(settings, video_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no audio for this clip")
        return FileResponse(path, media_type="audio/wav")

    return app
