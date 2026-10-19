"""Noise and music beds: the manifest, license rules, download and loading.

The manifest (`datasets/beds/beds.json`, committed) holds only where each bed comes from and its
license. `clipbench sweep fetch-beds` downloads the files into `data/beds/` (gitignored), checks the
sha1 the source publishes, and converts each to a 16 kHz mono WAV `data/beds/<id>.wav`. Synthetic beds
(`source = "synthetic"`) are generated on the fly from a seed and never downloaded.

License rule: a bed is usable only when its license is one of ALLOWED_LICENSES (CC0, public domain,
CC BY, CC BY-SA, all of which allow commercial use) and the manifest says `commercial_use: true`.
Anything with NC or ND, or a license we cannot read, is excluded.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from clipbench.config import Settings, get_settings

SAMPLE_RATE = 16_000
BED_KINDS = ("music", "noise")
USER_AGENT = "clipbench/0.1 (speech-to-text benchmark; https://github.com/Abhishek-B-R/clipbench)"

_ALLOWED = re.compile(r"^(cc0|public domain|pd|cc by( \d(\.\d)?)?( [a-z]{2})?|cc by-sa( \d(\.\d)?)?( [a-z]{2})?)$")
_FORBIDDEN = re.compile(r"\b(nc|nd|non[- ]?commercial|no[- ]?deriv)", re.IGNORECASE)


def license_ok(license_name: str | None) -> bool:
    """True for CC0, public domain, CC BY and CC BY-SA (any version or port). False for NC, ND or unknown."""
    if not license_name or not isinstance(license_name, str):
        return False
    name = license_name.strip().lower()
    if _FORBIDDEN.search(name):
        return False
    return bool(_ALLOWED.match(name))


@dataclass
class Bed:
    id: str
    kind: str
    source: str
    license: str | None
    description: str = ""
    title: str | None = None
    page_url: str | None = None
    download_url: str | None = None
    sha1: str | None = None
    size_bytes: int | None = None
    duration_s: float | None = None
    license_url: str | None = None
    attribution: str | None = None
    commercial_use: bool = False
    speech_free: str | None = None
    listened: bool = False
    generator: dict[str, Any] = field(default_factory=dict)

    @property
    def synthetic(self) -> bool:
        return self.source == "synthetic"

    @property
    def usable(self) -> bool:
        return self.kind in BED_KINDS and self.commercial_use and license_ok(self.license)

    def public(self) -> dict[str, Any]:
        """What the export shows about a bed: id, kind, source page and license. No audio."""
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "page_url": self.page_url,
            "license": self.license,
            "license_url": self.license_url,
            "attribution": self.attribution,
        }


def manifest_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.root / "datasets" / "beds" / "beds.json"


def beds_dir(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.data_dir / "beds"


def load_manifest(path: Path | None = None, settings: Settings | None = None) -> list[Bed]:
    path = path or manifest_path(settings)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("beds", raw) if isinstance(raw, dict) else raw
    known = set(Bed.__dataclass_fields__)
    return [Bed(**{k: v for k, v in e.items() if k in known}) for e in entries]


def usable_beds(beds: list[Bed], kinds: tuple[str, ...] | list[str] = BED_KINDS) -> list[Bed]:
    return [b for b in beds if b.usable and b.kind in kinds]


# ---------------------------------------------------------------- synthetic beds


def synthetic_bed(kind: str, seconds: float, seed: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Coloured noise from a seed: 'white', 'pink' (1/f power) or 'brown' (1/f^2). Peak normalised to 0.5."""
    n = int(round(seconds * sr))
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    if kind == "white":
        x = white
    else:
        spec = np.fft.rfft(white)
        f = np.fft.rfftfreq(n, 1 / sr)
        f[0] = f[1] if len(f) > 1 else 1.0
        power = {"pink": 1.0, "brown": 2.0}.get(kind)
        if power is None:
            raise ValueError(f"unknown synthetic bed type {kind!r}")
        spec = spec / f ** (power / 2)
        x = np.fft.irfft(spec, n)
    x = x - x.mean()
    peak = float(np.max(np.abs(x))) or 1.0
    return (0.5 * x / peak).astype(np.float32)


def _seed_of(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def bed_wav_path(bed: Bed, settings: Settings | None = None) -> Path:
    return beds_dir(settings) / f"{bed.id}.wav"


def bed_available(bed: Bed, settings: Settings | None = None) -> bool:
    return bed.synthetic or bed_wav_path(bed, settings).is_file()


def load_bed(bed: Bed, settings: Settings | None = None) -> np.ndarray:
    """The bed as float32 mono at 16 kHz."""
    if bed.synthetic:
        gen = bed.generator or {}
        return synthetic_bed(str(gen.get("type", "pink")), float(gen.get("seconds", 120)), _seed_of(bed.id))
    path = bed_wav_path(bed, settings)
    if not path.is_file():
        raise FileNotFoundError(f"bed {bed.id} is not downloaded ({path}); run `clipbench sweep fetch-beds`")
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"bed {bed.id} is {sr} Hz, expected {SAMPLE_RATE}")
    return audio.astype(np.float32)


# ---------------------------------------------------------------- download


def _http_download(url: str, dst: Path) -> None:
    import httpx

    tmp = dst.with_name(dst.name + ".part")
    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(dst)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_wav(src: Path, dst: Path) -> tuple[bool, str]:
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    args = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar",
            str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp)]  # fmt: skip
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False, (proc.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][:300]
    tmp.replace(dst)
    return True, ""


@dataclass
class FetchSummary:
    ready: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def fetch_beds(
    settings: Settings | None = None,
    *,
    beds: list[Bed] | None = None,
    only: list[str] | None = None,
    gap_s: float = 2.0,
    downloader: Callable[[str, Path], None] = _http_download,
    converter: Callable[[Path, Path], tuple[bool, str]] = _to_wav,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchSummary:
    """Download every usable, non-synthetic bed that is not on disk yet, one at a time."""
    settings = settings or get_settings()
    beds = beds if beds is not None else load_manifest(settings=settings)
    out_dir = beds_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = FetchSummary()
    last: float | None = None
    for bed in beds:
        if only and bed.id not in only:
            continue
        if not bed.usable:
            summary.excluded[bed.id] = f"license {bed.license!r} not allowed (or commercial_use false)"
            continue
        if bed.synthetic:
            summary.ready.append(bed.id)
            continue
        wav = bed_wav_path(bed, settings)
        if wav.is_file():
            summary.ready.append(bed.id)
            continue
        if not bed.download_url:
            summary.failed[bed.id] = "no download_url"
            continue
        suffix = Path(bed.download_url.split("?")[0]).suffix or ".bin"
        original = out_dir / f"{bed.id}.orig{suffix}"
        if not original.is_file():
            if last is not None:
                wait = gap_s - (time.monotonic() - last)
                if wait > 0:
                    sleep(wait)
            try:
                downloader(bed.download_url, original)
            except Exception as exc:
                summary.failed[bed.id] = f"download failed: {type(exc).__name__}: {exc}"[:300]
                log(f"{bed.id}: {summary.failed[bed.id]}")
                last = time.monotonic()
                continue
            last = time.monotonic()
            summary.downloaded.append(bed.id)
        if bed.sha1 and _sha1(original) != bed.sha1:
            original.unlink(missing_ok=True)
            summary.failed[bed.id] = "sha1 mismatch with the manifest (file changed at the source?)"
            log(f"{bed.id}: {summary.failed[bed.id]}")
            continue
        ok, err = converter(original, wav)
        if not ok:
            summary.failed[bed.id] = f"ffmpeg: {err}"
            log(f"{bed.id}: {summary.failed[bed.id]}")
            continue
        summary.ready.append(bed.id)
        log(f"{bed.id}: ready ({bed.license})")
    return summary
