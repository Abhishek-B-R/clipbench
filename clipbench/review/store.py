"""Storage for the review UI: the queue, verified files, audio, waveform peaks and creator captions.

The queue is data/sample.parquet ordered by sample_rank. Clips listed in data/rereview.json
(written by error analysis) come first. Verified files follow docs/INTERFACES.md plus the
"Reference additions" in docs/V2.md, and are written atomically (tmp file + rename).

Clip ids are `<video_id>` for a whole video or `<video_id>@<start>-<end>` for a window.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from clipbench import models
from clipbench.config import Settings
from clipbench.models import TAGS

# Platform ids are [A-Za-z0-9_-]; a window adds "@<start>-<end>". No dots or slashes, so a clip id
# can never leave data/verified or data/audio.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}(?:@\d{1,9}-\d{1,9})?$")
WINDOW_RE = re.compile(r"^(?P<vid>[A-Za-z0-9_-]+)@(?P<start>\d+)-(?P<end>\d+)$")
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{1,8})?$")

CATEGORIES: list[str] = list(
    getattr(
        models,
        "CATEGORIES",
        ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer", "other"],
    )
)
NONSPEECH_KINDS: list[str] = list(getattr(models, "NONSPEECH_KINDS", ["music", "sfx", "silence"]))
CONDITIONS: list[str] = list(models.CONDITIONS)
ENTITY_TYPES: list[str] = list(models.ENTITY_TYPES)
ITN_KINDS: list[str] = list(models.ITN_KINDS)
SPEAKER_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
# Text span fields of a verified file (docs/V3.md). All hold code point offsets into `text`.
TEXT_SPAN_FIELDS = ("codeswitch_spans", "entity_spans", "alternatives", "itn_spans")
MAX_OPTIONS = 10
MAX_OPTION_LEN = 200


@dataclass
class QueueItem:
    video_id: str  # the clip id
    language: str
    channel: str | None
    duration_s: float | None
    flagged: bool = False
    source_id: str | None = None  # raw platform id
    start_ms: int | None = None  # window start, None for a whole video
    end_ms: int | None = None
    category: str | None = None  # prefill from sample, manifest or datasets/<lang>/clips.csv
    platform: str = "youtube"

    @property
    def kind(self) -> str:
        return "window" if self.start_ms is not None else "short"

    @property
    def youtube_url(self) -> str:
        """Link to the clip on its platform (the name is kept for the page's existing field)."""
        vid = self.source_id or self.video_id
        if self.platform != "youtube":
            from clipbench.data.datasets import public_url

            return public_url(self.platform, vid)
        if self.start_ms is None:
            return f"https://www.youtube.com/shorts/{vid}"
        return f"https://www.youtube.com/watch?v={vid}&t={self.start_ms // 1000}"


@dataclass
class Queue:
    items: list[QueueItem]
    by_id: dict[str, QueueItem] = field(default_factory=dict)
    flagged_since: float | None = None  # mtime of rereview.json

    def __post_init__(self) -> None:
        self.by_id = {it.video_id: it for it in self.items}
        self._pos = {it.video_id: i for i, it in enumerate(self.items)}

    def index_of(self, video_id: str) -> int:
        return self._pos[video_id]


def rereview_path(settings: Settings) -> Path:
    return settings.data_dir / "rereview.json"


def load_rereview_ids(settings: Settings) -> list[str]:
    path = rereview_path(settings)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):  # tolerate {"video_ids": [...]}
        raw = raw.get("video_ids", [])
    return [str(v) for v in raw if isinstance(v, str)]


def _clean(value: Any) -> Any:
    """Turn pandas NA/NaN into None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        import pandas as pd

        if not isinstance(value, (list, dict, str)) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _category(value: Any) -> str | None:
    value = _clean(value)
    return value if isinstance(value, str) and value in CATEGORIES else None


def _manifest_categories(settings: Settings) -> dict[str, str]:
    """video_id -> category from the manifest, when it has that column."""
    import pandas as pd

    if not settings.manifest_path.exists():
        return {}
    try:
        m = pd.read_parquet(settings.manifest_path)
    except Exception:  # an unreadable manifest only loses the prefill
        return {}
    if "category" not in m.columns or "video_id" not in m.columns:
        return {}
    out = {}
    for vid, cat in zip(m["video_id"], m["category"], strict=False):
        if (c := _category(cat)) is not None:
            out[str(vid)] = c
    return out


def _dataset_categories(settings: Settings, language: str) -> dict[str, str]:
    """clip id (and plain video id) -> category from datasets/<lang>/clips.csv (docs/V2.md)."""
    path = settings.root / "datasets" / language / "clips.csv"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                cat = _category(row.get("category"))
                vid = (row.get("video_id") or "").strip()
                if not cat or not vid:
                    continue
                start, end = (row.get("start_ms") or "").strip(), (row.get("end_ms") or "").strip()
                out[f"{vid}@{start}-{end}" if start and end else vid] = cat
                out.setdefault(vid, cat)
    except (OSError, csv.Error):
        return {}
    return out


def _window_ms(row: dict, clip_id: str) -> tuple[int | None, int | None]:
    """Window bounds in ms: sample columns first, then the clip id (ms per docs/V2.md)."""
    for start_col, end_col, scale in (("start_ms", "end_ms", 1), ("window_start_s", "window_end_s", 1000)):
        start, end = _clean(row.get(start_col)), _clean(row.get(end_col))
        if start is not None and end is not None:
            return int(round(float(start) * scale)), int(round(float(end) * scale))
    m = WINDOW_RE.match(clip_id)
    if m:
        return int(m["start"]), int(m["end"])
    return None, None


def load_queue(settings: Settings, only_rereview: bool = False) -> Queue:
    import pandas as pd

    if not settings.sample_path.exists():
        return Queue(items=[])
    df = pd.read_parquet(settings.sample_path)
    if "sample_rank" in df.columns:
        df = df.sort_values("sample_rank", kind="stable")
    manifest_cats: dict[str, str] | None = None
    dataset_cats: dict[str, dict[str, str]] = {}
    items: list[QueueItem] = []
    for row in df.to_dict("records"):
        clip_id = str(_clean(row.get("clip_id")) or row["video_id"])
        if not VIDEO_ID_RE.match(clip_id):
            continue
        source = _clean(row.get("source_id")) or _clean(row.get("youtube_id"))
        if source is None:
            m = WINDOW_RE.match(clip_id)
            source = m["vid"] if m else clip_id
        language = _clean(row.get("language")) or settings.language
        start_ms, end_ms = _window_ms(row, clip_id)
        category = _category(row.get("category"))
        if category is None:
            if manifest_cats is None:
                manifest_cats = _manifest_categories(settings)
            if language not in dataset_cats:
                dataset_cats[language] = _dataset_categories(settings, language)
            ds = dataset_cats[language]
            category = ds.get(clip_id) or manifest_cats.get(str(source)) or ds.get(str(source))
        dur = _clean(row.get("duration_s"))
        items.append(
            QueueItem(
                video_id=clip_id,
                language=language,
                channel=_clean(row.get("channel")),
                duration_s=float(dur) if dur is not None else None,
                source_id=str(source),
                start_ms=start_ms,
                end_ms=end_ms,
                category=category,
                platform=str(_clean(row.get("platform")) or "youtube"),
            )
        )
    flagged = set(load_rereview_ids(settings))
    for it in items:
        it.flagged = it.video_id in flagged
    first = [it for it in items if it.flagged]
    rest = [] if only_rereview else [it for it in items if not it.flagged]
    path = rereview_path(settings)
    since = path.stat().st_mtime if flagged and path.exists() else None
    return Queue(items=first + rest, flagged_since=since)


def verified_path(settings: Settings, video_id: str) -> Path:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"bad clip id: {video_id!r}")
    return settings.verified_dir / f"{video_id}.json"


def read_verified(settings: Settings, video_id: str) -> dict | None:
    path = verified_path(settings, video_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip_status(settings: Settings, queue: Queue, item: QueueItem) -> str:
    """One of "todo", "reviewed", "skipped", "rereview".

    A flagged clip counts as done only once it was saved after rereview.json was written.
    """
    rec = read_verified(settings, item.video_id)
    if rec is None:
        return "rereview" if item.flagged else "todo"
    if item.flagged and queue.flagged_since is not None:
        ts = _parse_ts(rec.get("reviewed_at"))
        # reviewed_at has whole seconds, so compare against the truncated mtime.
        if ts is None or ts < int(queue.flagged_since):
            return "rereview"
    return "skipped" if rec.get("skipped") else "reviewed"


def audio_path(settings: Settings, video_id: str) -> Path:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"bad clip id: {video_id!r}")
    return settings.audio_dir / f"{video_id}.wav"


def audio_sha256(settings: Settings, video_id: str) -> str | None:
    """sha256 of the WAV from hashes.json, computed from the file when missing there."""
    hashes = settings.audio_dir / "hashes.json"
    if hashes.exists():
        try:
            entry = json.loads(hashes.read_text(encoding="utf-8")).get(video_id)
            if isinstance(entry, dict) and entry.get("wav"):
                return str(entry["wav"])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    path = audio_path(settings, video_id)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def waveform_peaks(path: Path, buckets: int) -> dict:
    """Downsampled min/max peaks (scaled to -1..1 by the loudest sample) for drawing a waveform."""
    import numpy as np
    import soundfile

    data, sr = soundfile.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    n = len(mono)
    duration_ms = int(round(1000 * n / sr)) if sr else 0
    if n == 0:
        return {"duration_ms": 0, "sample_rate": sr, "min": [], "max": []}
    buckets = max(1, min(buckets, n))
    edges = np.linspace(0, n, buckets + 1).astype(int)
    lo = np.minimum.reduceat(mono, edges[:-1])
    hi = np.maximum.reduceat(mono, edges[:-1])
    scale = float(max(np.abs(lo).max(), np.abs(hi).max())) or 1.0
    return {
        "duration_ms": duration_ms,
        "sample_rate": sr,
        "min": [round(float(v) / scale, 3) for v in lo],
        "max": [round(float(v) / scale, 3) for v in hi],
    }


def load_creator(video_id: str, settings: Settings) -> tuple[str, list[dict] | None]:
    """Creator caption text and words. Imported lazily so tests can swap the module."""
    from clipbench.data.references import load_reference

    ref = load_reference(video_id, "creator", settings)
    if ref is None:
        return "", None
    words = [w.model_dump() if hasattr(w, "model_dump") else dict(w) for w in (ref.words or [])] or None
    return ref.text or "", words


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clean_tags(tags: list[str]) -> list[str]:
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise ValueError(f"unknown tags: {unknown}")
    return [t for t in TAGS if t in set(tags)]  # canonical order, no duplicates


def clean_category(category: str | None) -> str | None:
    if category in (None, ""):
        return None
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category!r}")
    return category


def clean_nonspeech(spans: list[dict], duration_ms: int | None = None) -> list[dict]:
    """Validate {start_ms, end_ms, kind} spans, clip them to the audio, sort by start."""
    out = []
    for s in spans:
        kind = s.get("kind")
        if kind not in NONSPEECH_KINDS:
            raise ValueError(f"unknown nonspeech kind: {kind!r}")
        try:
            start, end = int(round(float(s["start_ms"]))), int(round(float(s["end_ms"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad nonspeech span: {s!r}") from exc
        start = max(0, start)
        if duration_ms:
            end = min(end, duration_ms)
        if end <= start:
            raise ValueError(f"empty nonspeech span: {s!r}")
        out.append({"start_ms": start, "end_ms": end, "kind": kind})
    return sorted(out, key=lambda s: (s["start_ms"], s["end_ms"]))


def reanchor_span(text: str, span_text: str, start: int) -> tuple[int, int] | None:
    """Where `span_text` now sits in `text`: at `start` if unchanged, else the nearest occurrence."""
    if not span_text:
        return None
    if text[start : start + len(span_text)] == span_text:
        return start, start + len(span_text)
    best = None
    i = text.find(span_text)
    while i != -1:
        if best is None or abs(i - start) < abs(best - start):
            best = i
        i = text.find(span_text, i + 1)
    return None if best is None else (best, best + len(span_text))


def utf16_to_index(text: str, units: int) -> int:
    """Code point index of a browser (UTF-16 code unit) offset. Past the end stays past the end."""
    seen = 0
    for i, ch in enumerate(text):
        if seen >= units:
            return i
        seen += 2 if ord(ch) > 0xFFFF else 1
    return len(text) + max(0, units - seen)


def index_to_utf16(text: str, index: int) -> int:
    """Browser (UTF-16 code unit) offset of a code point index."""
    return index + sum(1 for ch in text[: max(0, index)] if ord(ch) > 0xFFFF)


def spans_from_browser(text: str, spans: list[dict]) -> list[dict]:
    """Code-switch spans posted by the review page, offsets converted to code points."""
    out = []
    for s in spans:
        s = dict(s)
        for key in ("start", "end"):
            if isinstance(s.get(key), int) and s[key] >= 0:
                s[key] = utf16_to_index(text, s[key])
        out.append(s)
    return out


def _anchor(text: str, s: dict, what: str) -> tuple[int, int] | None:
    """Final (start, end) of a text span, re-anchored by its `text` when the offsets went stale.

    None means the span no longer fits the text (dropped). Malformed offsets raise ValueError.
    """
    try:
        start, end = int(s["start"]), int(s["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"bad {what} span: {s!r}") from exc
    anchor = s.get("text")
    if isinstance(anchor, str) and anchor:
        pos = reanchor_span(text, anchor, start)
        if pos is None:
            return None
        start, end = pos
    if not (0 <= start < end <= len(text)):
        return None
    return start, end


def _clean_text_spans(text: str, spans: list[dict], what: str, extra) -> tuple[list[dict], list[dict]]:
    """Shared re-anchoring for text spans. `extra(span)` validates and returns the span's own fields
    (raising ValueError on a bad value, even for a span that would be dropped)."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for s in spans:
        fields = extra(s)
        pos = _anchor(text, s, what)
        if pos is None:
            dropped.append({"start": s.get("start"), "end": s.get("end"), **fields, "text": s.get("text")})
            continue
        kept.append({"start": pos[0], "end": pos[1], **fields})
    kept.sort(key=lambda s: (s["start"], s["end"]))
    # Drop exact duplicates; overlapping spans are left as tagged.
    unique = [s for i, s in enumerate(kept) if i == 0 or s != kept[i - 1]]
    return unique, dropped


def clean_codeswitch(text: str, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    """Validate {start, end, lang, text?} spans against the final text.

    When a span carries the `text` it was tagged on and the offsets no longer point at it, the
    span is re-anchored to the nearest occurrence; spans that cannot be found are dropped and
    returned separately so the UI can warn. Returns (kept, dropped); kept spans have start, end, lang.
    """

    def extra(s: dict) -> dict:
        lang = s.get("lang") or "en"
        if not isinstance(lang, str) or not LANG_RE.match(lang):
            raise ValueError(f"bad code-switch language: {lang!r}")
        return {"lang": lang}

    return _clean_text_spans(text, spans, "code-switch", extra)


def clean_entities(text: str, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    """{start, end, type, text?} proper noun / brand spans (B17); type in ENTITY_TYPES."""

    def extra(s: dict) -> dict:
        kind = s.get("type") or "other"
        if kind not in ENTITY_TYPES:
            raise ValueError(f"unknown entity type: {kind!r}")
        return {"type": kind}

    return _clean_text_spans(text, spans, "entity", extra)


def clean_itn(text: str, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    """{start, end, kind, text?} spans whose written form matters (A6); kind in ITN_KINDS."""

    def extra(s: dict) -> dict:
        kind = s.get("kind") or "number"
        if kind not in ITN_KINDS:
            raise ValueError(f"unknown ITN kind: {kind!r}")
        return {"kind": kind}

    return _clean_text_spans(text, spans, "ITN", extra)


def clean_alternatives(text: str, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    """{start, end, options, text?} valid alternative spellings (A7).

    Saved options always start with the reference text at [start:end], then the other spellings
    (stripped, de-duplicated). A span left with no other spelling is dropped. Alternative spans must
    not overlap each other, so the scorer's lattice has one path per stretch of text.
    """

    def extra(s: dict) -> dict:
        options = s.get("options")
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise ValueError(f"bad alternatives options: {options!r}")
        if len(options) > MAX_OPTIONS or any(len(o) > MAX_OPTION_LEN for o in options):
            raise ValueError("too many or too long alternative options")
        return {"options": [o.strip() for o in options]}

    kept, dropped = _clean_text_spans(text, spans, "alternative", extra)
    out: list[dict] = []
    for s in kept:
        ref = text[s["start"] : s["end"]]
        others: list[str] = []
        for o in s["options"]:
            if o and o != ref and o not in others:
                others.append(o)
        if not others:
            dropped.append({**s, "text": ref})
            continue
        out.append({"start": s["start"], "end": s["end"], "options": [ref, *others]})
    for a, b in zip(out, out[1:], strict=False):
        if b["start"] < a["end"]:
            raise ValueError(f"alternative spans overlap: {text[a['start']:a['end']]!r} and {text[b['start']:b['end']]!r}")
    return out, dropped


def clean_conditions(conditions: list[str], tags: list[str], nonspeech_spans: list[dict]) -> list[str]:
    """Audio conditions (A4) in CONDITIONS order. `music` is implied by the background_music tag or a
    music non-speech span (docs/V3.md)."""
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        raise ValueError(f"unknown conditions: {unknown}")
    chosen = set(conditions)
    if "background_music" in tags or any(s.get("kind") == "music" for s in nonspeech_spans):
        chosen.add("music")
    return [c for c in CONDITIONS if c in chosen]


def clean_speaker_turns(turns: list[dict], duration_ms: int | None = None) -> list[dict]:
    """Validate {start_ms, end_ms, speaker} turns (B15): clip to the audio, sort, and refuse two turns of
    the same speaker that overlap (different speakers may overlap: crosstalk)."""
    out = []
    for t in turns:
        speaker = t.get("speaker")
        speaker = speaker.strip() if isinstance(speaker, str) else speaker
        if not isinstance(speaker, str) or not SPEAKER_RE.match(speaker):
            raise ValueError(f"bad speaker label: {speaker!r}")
        try:
            start, end = int(round(float(t["start_ms"]))), int(round(float(t["end_ms"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad speaker turn: {t!r}") from exc
        start = max(0, start)
        if duration_ms:
            end = min(end, duration_ms)
        if end <= start:
            raise ValueError(f"empty speaker turn (or past the end of the audio): {t!r}")
        out.append({"start_ms": start, "end_ms": end, "speaker": speaker})
    out.sort(key=lambda t: (t["start_ms"], t["end_ms"], t["speaker"]))
    last_end: dict[str, int] = {}
    for t in out:
        if t["start_ms"] < last_end.get(t["speaker"], -1):
            raise ValueError(f"speaker {t['speaker']} has overlapping turns at {t['start_ms']} ms")
        last_end[t["speaker"]] = max(last_end.get(t["speaker"], -1), t["end_ms"])
    return out


def text_spans_for_browser(text: str, field: str, spans: list[dict] | None) -> list[dict]:
    """Saved text spans as the page wants them: UTF-16 offsets plus the anchor `text`. Alternatives
    list only the other spellings (the reference text is implied)."""
    out = []
    for sp in spans or []:
        try:
            start, end = int(sp["start"]), int(sp["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= start < end <= len(text)):
            continue
        item = {
            **sp,
            "start": index_to_utf16(text, start),
            "end": index_to_utf16(text, end),
            "text": text[start:end],
        }
        if field == "alternatives":
            item["options"] = [o for o in sp.get("options") or [] if isinstance(o, str) and o != text[start:end]]
        out.append(item)
    return out


_EN_VARIANTS = {
    "ok": ["okay"], "okay": ["ok"], "alright": ["all right"], "all right": ["alright"],
    "gonna": ["going to"], "wanna": ["want to"], "gotta": ["got to"], "kinda": ["kind of"],
    "percent": ["%"], "%": ["percent"],
}  # fmt: skip


@lru_cache(maxsize=8)
def _number_words(language: str) -> dict[str, str]:
    """Spelled-out number (lowercase) -> digits, for the languages normalize.py can spell."""
    from clipbench.scoring import normalize as nz

    speller = {
        "en": nz.english_number_words,
        "es": nz.spanish_number_words,
        "ja": nz.japanese_number_reading,
    }.get(language)
    if speller is None:
        return {}
    top = 10000 if language == "ja" else 1000
    return {speller(n).lower(): str(n) for n in range(top + 1)}


def suggest_alternatives(language: str, text: str) -> list[str]:
    """Quick alternative spellings for a selected span: digits vs words, the kana reading for ja (via
    the scorer's reading normaliser), a Hinglish romanisation for Devanagari, a few en variants."""
    from clipbench.scoring import normalize as nz

    lang = (language or "").split("-")[0].lower()
    t = text.strip()
    if not t:
        return []
    out: list[str] = []
    digits = nz.nfkc(t).replace(",", "")
    if re.fullmatch(r"\d{1,15}", digits):
        n = int(digits)
        if digits != t:
            out.append(digits)
        if lang == "ja":
            out.append(nz.japanese_number_reading(n))
        elif lang == "es":
            out.append(nz.spanish_number_words(n))
        elif lang in ("en", "hi"):
            out.append(nz.english_number_words(n))
        if n >= 1000 and lang in ("en", "es", "hi"):
            out.append(f"{n:,}")
    elif (num := _number_words(lang).get(re.sub(r"[\s-]+", " ", t.lower()))) is not None:
        out.append(num)
    if lang == "ja":
        if re.search(r"[\u4e00-\u9fff\u3400-\u4dbf\u30a0-\u30ff々]", t):
            try:
                out.append(nz.japanese_reading(t))
            except Exception:  # no fugashi / unidic: try pykakasi alone, else nothing
                try:
                    out.append(nz._pykakasi_reading(t))
                except Exception:
                    pass
        elif re.fullmatch(r"[\u3041-\u3096ー]+", t):
            out.append("".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in t))
    if lang == "hi" and re.search(r"[\u0900-\u097f]", t):
        out.append(nz.transliterate_devanagari(t))
    if lang == "en":
        out.extend(_EN_VARIANTS.get(t.lower(), []))
    seen: list[str] = []
    for o in out:
        o = o.strip()
        if o and o != t and o not in seen:
            seen.append(o)
    return seen[:6]


def build_record(
    settings: Settings,
    item: QueueItem,
    *,
    text: str,
    original_text: str,
    tags: list[str],
    reviewer: str,
    skipped: bool,
    category: str | None = None,
    nonspeech_spans: list[dict] | None = None,
    codeswitch_spans: list[dict] | None = None,
    punctuated: bool = False,
    conditions: list[str] | None = None,
    speaker_turns: list[dict] | None = None,
    entity_spans: list[dict] | None = None,
    alternatives: list[dict] | None = None,
    itn_spans: list[dict] | None = None,
) -> dict:
    """The verified file (docs/INTERFACES.md plus V2.md and V3.md additions). Spans and turns must
    already be cleaned (clean_nonspeech, clean_codeswitch, clean_speaker_turns, ...); conditions are
    cleaned here because `music` follows from the tags and the non-speech spans."""
    tags = clean_tags(tags)
    return {
        "video_id": item.video_id,
        "language": item.language,
        "text": text,
        "original_text": original_text,
        "tags": tags,
        "reviewer": reviewer,
        "reviewed_at": utc_now_iso(),
        "audio_sha256": audio_sha256(settings, item.video_id),
        "skipped": skipped,
        "category": clean_category(category),
        "nonspeech_spans": nonspeech_spans or [],
        "codeswitch_spans": codeswitch_spans or [],
        "punctuated": bool(punctuated),
        "conditions": clean_conditions(conditions or [], tags, nonspeech_spans or []),
        "speaker_turns": speaker_turns or [],
        "entity_spans": entity_spans or [],
        "alternatives": alternatives or [],
        "itn_spans": itn_spans or [],
    }


def save_verified(settings: Settings, record: dict) -> Path:
    path = verified_path(settings, record["video_id"])
    write_json_atomic(path, record)
    return path
