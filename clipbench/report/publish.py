"""clipbench publish --check: make sure nothing we publish leaks audio or caption text.

Fails when any file under site/ or results/ is:
- audio or video, by extension (AUDIO_EXTENSIONS) or by magic bytes (RIFF, ID3, OggS, fLaC, ftyp at
  offset 4, EBML 1A45DFA3 for webm/mkv);
- or contains more than MAX_WORDS (8) consecutive words of any reference text.

Reference texts: data/runs/*/refs.parquet (ref_text), data/verified/*.json (text and original_text),
and the creator tracks of data/sample.parquet through clipbench.data.references (optional).

Words:
- spaced text (es, spaced ko): whitespace tokens after normalisation (NFKC, lowercase, punctuation
  and symbols removed). Every 9-token window of a reference is hashed; each scanned file is
  normalised the same way and its 9-token windows are looked up in that set.
- unspaced text (ja, and ko without spaces): words come from the scoring word tokenizer when it has
  one, else fugashi. Each 9-word window is stored as its character string; scanned files are reduced
  to a character stream (whitespace dropped) and searched for those strings through a prefix index.
  Matching on characters keeps the check robust to MeCab segmenting the same span differently
  inside a different context. If no tokenizer is available, 8 words count as 12 characters, so any
  13 character window of a reference fails the check.

Scanned file types: .md .html .htm .json .csv .txt .js .svg (as text, HTML entities and \\uXXXX
escapes decoded, JSON parsed to strings) and .parquet (every string column, cell by cell).
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from clipbench.config import Settings, get_settings

MAX_WORDS = 8
WINDOW = MAX_WORDS + 1
FALLBACK_CHARS = 12  # 8 words ~ 12 characters for unspaced text without a tokenizer
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".opus", ".ogg", ".flac", ".aac", ".mp4", ".mkv"}
TEXT_EXTENSIONS = {".md", ".html", ".htm", ".json", ".csv", ".txt", ".js", ".svg"}
SCAN_DIRS = ("site", "results")

_CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿ｦ-ﾟ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


@dataclass
class Offender:
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


# ---------------------------------------------------------------- audio


def audio_reason(path: Path) -> str | None:
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return f"audio/video file extension {path.suffix.lower()}"
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
    except OSError:
        return None
    if head.startswith(b"RIFF"):
        return "audio magic bytes RIFF"
    if head.startswith(b"ID3"):
        return "audio magic bytes ID3"
    if head.startswith(b"OggS"):
        return "audio magic bytes OggS"
    if head.startswith(b"fLaC"):
        return "audio magic bytes fLaC"
    if head[4:8] == b"ftyp":
        return "audio/video magic bytes ftyp (mp4/m4a)"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/video magic bytes EBML (webm/mkv)"
    return None


# ---------------------------------------------------------------- normalisation and words


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(" " if unicodedata.category(c)[0] in "PSC" else c for c in text)


def is_unspaced(text: str) -> bool:
    """Japanese text, or Korean written without spaces."""
    if _CJK.search(text):
        return True
    if _HANGUL.search(text):
        return len(text.split()) <= 1 or len(text) / max(len(text.split()), 1) > 12
    return False


@lru_cache(maxsize=1)
def _word_tokenizer():
    """Return (name, fn) for unspaced word tokenisation, or (None, None)."""
    try:
        from clipbench.scoring import normalize as norm

        for attr in ("word_tokens", "tokenize_words", "japanese_words"):
            fn = getattr(norm, attr, None)
            if callable(fn):
                return attr, fn
    except Exception:  # noqa: BLE001
        pass
    try:
        import fugashi

        tagger = fugashi.Tagger()
        return "fugashi", lambda text: [w.surface for w in tagger(text)]
    except Exception:  # noqa: BLE001
        return None, None


def unspaced_windows(text: str) -> set[str]:
    """Character strings of every (MAX_WORDS + 1) word window of an unspaced reference."""
    norm = normalise(text)
    _, fn = _word_tokenizer()
    out: set[str] = set()
    if fn is None:
        chars = "".join(norm.split())
        n = FALLBACK_CHARS + 1
        return {chars[i : i + n] for i in range(len(chars) - n + 1)}
    for chunk in norm.split():
        words = [w for w in fn(chunk) if w.strip()]
        for i in range(len(words) - WINDOW + 1):
            out.add("".join(words[i : i + WINDOW]))
    # Windows can also cross a space in the reference: tokenise the whole text without spaces too.
    words = [w for w in fn("".join(norm.split())) if w.strip()]
    for i in range(len(words) - WINDOW + 1):
        out.add("".join(words[i : i + WINDOW]))
    return out


# ---------------------------------------------------------------- index


class ReferenceIndex:
    def __init__(self) -> None:
        self.grams: set[int] = set()
        self.windows_by_prefix: dict[str, set[str]] = {}
        self.prefix_len = WINDOW
        self.n_refs = 0

    def add(self, text: str) -> None:
        if not text or not text.strip():
            return
        self.n_refs += 1
        if is_unspaced(text):
            for w in unspaced_windows(text):
                if len(w) >= self.prefix_len:
                    self.windows_by_prefix.setdefault(w[: self.prefix_len], set()).add(w)
        else:
            toks = normalise(text).split()
            for i in range(len(toks) - WINDOW + 1):
                self.grams.add(hash(tuple(toks[i : i + WINDOW])))

    def find(self, text: str) -> str | None:
        """First leaked span in `text`, or None."""
        if self.grams:
            toks = normalise(text).split()
            for i in range(len(toks) - WINDOW + 1):
                if hash(tuple(toks[i : i + WINDOW])) in self.grams:
                    return " ".join(toks[i : i + WINDOW])
        if self.windows_by_prefix:
            chars = "".join(normalise(text).split())
            k = self.prefix_len
            for i in range(len(chars) - k + 1):
                cands = self.windows_by_prefix.get(chars[i : i + k])
                if cands:
                    for w in cands:
                        if chars.startswith(w, i):
                            return w
        return None


def reference_texts(settings: Settings) -> Iterator[str]:
    import pandas as pd

    for path in sorted(settings.runs_text_dir.glob("*/refs.parquet")):
        try:
            df = pd.read_parquet(path, columns=["ref_text"])
        except Exception:  # noqa: BLE001
            continue
        for t in df["ref_text"].dropna():
            yield str(t)
    for path in sorted(settings.verified_dir.glob("*.json")):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for key in ("text", "original_text"):
            if isinstance(d.get(key), str):
                yield d[key]
    if settings.sample_path.exists():
        try:
            from clipbench.data.references import load_reference
        except ImportError:
            return
        try:
            sample = pd.read_parquet(settings.sample_path, columns=["video_id"])
        except Exception:  # noqa: BLE001
            return
        for vid in sample["video_id"].dropna():
            try:
                ref = load_reference(str(vid), "creator", settings=settings)
            except Exception:  # noqa: BLE001
                continue
            if ref is not None and ref.text:
                yield ref.text


def build_index(texts: Iterable[str]) -> ReferenceIndex:
    idx = ReferenceIndex()
    for t in texts:
        idx.add(t)
    return idx


# ---------------------------------------------------------------- file scanning


def _decode_escapes(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16))

    text = _UESC.sub(repl, text)
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _json_strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _json_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _json_strings(v)


def file_texts(path: Path) -> Iterator[str]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        import pandas as pd

        try:
            df = pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            return
        for col in df.columns:
            if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
                for v in df[col].dropna():
                    if isinstance(v, str):
                        yield v
        return
    if suffix not in TEXT_EXTENSIONS:
        return
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return
    if suffix == ".json":
        try:
            yield from _json_strings(json.loads(raw))
            return
        except ValueError:
            pass
    text = _decode_escapes(raw)
    if suffix in {".html", ".htm", ".svg", ".md"}:
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    yield text


def iter_files(settings: Settings) -> Iterator[Path]:
    for root in (settings.site_dir, settings.results_dir):
        if root.exists():
            yield from sorted(p for p in root.rglob("*") if p.is_file())


def gitignore_covers_data(root: Path) -> bool:
    path = root / ".gitignore"
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        line = line.strip()
        if line in {"data/", "/data/", "data", "/data", "data/*", "/data/*", "data/**", "/data/**"}:
            return True
    return False


def check(settings: Settings | None = None, index: ReferenceIndex | None = None) -> list[Offender]:
    settings = settings or get_settings()
    offenders: list[Offender] = []
    if not gitignore_covers_data(settings.root):
        offenders.append(Offender(settings.root / ".gitignore", "does not ignore data/"))
    index = index or build_index(reference_texts(settings))
    for path in iter_files(settings):
        reason = audio_reason(path)
        if reason:
            offenders.append(Offender(path, reason))
            continue
        for text in file_texts(path):
            leak = index.find(text)
            if leak:
                offenders.append(
                    Offender(
                        path, f"contains more than {MAX_WORDS} consecutive reference words: {leak[:24]}..."
                    )
                )
                break
    return offenders
