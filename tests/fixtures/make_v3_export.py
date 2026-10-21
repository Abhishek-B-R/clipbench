"""Build a REAL v3 export from the Python pipeline, for the web tests (web/test/fixtures/python-export/).

Everything is synthetic: clip ids `synth00001`..., made-up English sentences, tone or constant audio, and seven
fake providers (`synth-a`..`synth-g`). No real caption, audio or result is read or written. The script runs the
real code paths in a temp data/results/web tree:

    runner.run (a hinted run, then a run with diarize + detect_language + keyterms), score_run, the v3 scorers
    through their export hooks (formatting, entities, style, diarization, lid, captions, reliability, compute),
    a streaming run on the in-memory fake server + score_stream, a sweep with synthetic beds + score_sweep,
    a long-form run + score_longform, four canary runs (history and drift), the semantic judge with a mocked
    transport and a synthetic calibration file, then report.export.export_run for both runs.

Run from the repo root:  .venv/bin/python tests/fixtures/make_v3_export.py
It copies index.json, runs/<id>.json and findings/en.json into web/test/fixtures/python-export/.

Publication gate (docs/V3.md, "Publication gate fields"): the main run must come out PUBLISHED. N_CLIPS verified
clips (>= 100) are spread over 40 channels (largest share 2.5%) and the six categories, each saved in a synthetic
verified file (data/verified/<clip_id>.json) because the diversity check reads categories from those files only.
`synth-a`..`synth-f` score (almost) every clip, so six providers are eligible. `synth-g` fails one clip in six
(coverage about 83%, below the 90% minimum) and has the lowest error rate, so a site that ranked it by error rate
instead of reading `ranking` would put it first: the web tests check that it is never ranked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import soundfile as sf  # noqa: E402
from rich.console import Console  # noqa: E402

SR = 16_000
LANG = "en"
OUT = ROOT / "web" / "test" / "fixtures" / "python-export"
N_CLIPS = 120
BASE_RUN = "20261001-0900-synth"
MAIN_RUN = "20261019-0900-synth"
QUIET = Console(stderr=True, quiet=True)

VOCAB = ["we", "went", "to", "the", "park", "and", "then", "had", "lunch", "with", "my", "friends", "it", "was",
         "really", "fun", "today", "this", "video", "shows", "how", "make", "a", "simple", "salad", "at", "home",
         "you", "can", "see", "new", "camera", "is", "great", "for", "travel", "every", "morning", "i", "walk"]  # fmt: skip
BRANDS = [("Zorblax", "brand"), ("Mirela", "person"), ("Quentaro", "place"), ("Vexlo", "product")]
CONDITIONS = ["music", "sfx", "multiple_speakers", "fast_speech", "whisper_shout", "outdoor_noise", "code_switching"]
CATEGORIES = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer"]
KEYTERMS = [b for b, _ in BRANDS]


def _env(tmp: Path) -> None:
    os.environ["CLIPBENCH_DATA_DIR"] = str(tmp / "data")
    os.environ["CLIPBENCH_RESULTS_DIR"] = str(tmp / "results")
    os.environ["CLIPBENCH_SITE_DIR"] = str(tmp / "site")
    os.environ["CLIPBENCH_WEB_DIR"] = str(tmp / "web")
    os.environ["CLIPBENCH_CANARY_DIR"] = str(tmp / "canary")
    os.environ.pop("CLIPBENCH_CANARY_BUDGET_USD", None)
    from clipbench.config import get_settings

    get_settings.cache_clear()


# ---------------------------------------------------------------- references


def _sentence(rng: random.Random, i: int) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Text plus entity, alternative and ITN spans (code point offsets)."""
    parts: list[tuple[str, str | None]] = []  # (token, span kind)
    n = rng.randint(9, 15)
    for k in range(n):
        if k == 2 and i % 3 == 0:
            parts.append(("um", None))
        if k == 4 and i % 2 == 0:
            parts.append((BRANDS[i % len(BRANDS)][0], "entity"))
        if k == 6 and i % 4 == 1:
            parts.append(("25", "itn"))
        if k == 8 and i % 5 == 2:
            parts.append(("okay", "alt"))
        parts.append((rng.choice(VOCAB), None))
    text, entities, alts, itn = "", [], [], []
    for j, (tok, kind) in enumerate(parts):
        if j == 0:
            tok = tok[0].upper() + tok[1:]
        start = len(text) + (1 if text else 0)
        text = f"{text} {tok}" if text else tok
        end = start + len(tok)
        if kind == "entity":
            entities.append({"start": start, "end": end, "type": BRANDS[i % len(BRANDS)][1]})
        elif kind == "itn":
            itn.append({"start": start, "end": end, "kind": "number"})
        elif kind == "alt":
            alts.append({"start": start, "end": end, "options": ["okay", "ok"]})
    return text + ".", entities, alts, itn


def _write_wav(path: Path, seconds: float, value: int | None = None, hz: float = 220.0) -> str:
    n = int(seconds * SR)
    if value is not None:
        x = np.full(n, value, dtype=np.int16)
    else:
        t = np.arange(n) / SR
        x = (0.2 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16)
    sf.write(str(path), x, SR, subtype="PCM_16")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_clips(audio_dir: Path) -> list:
    from clipbench.config import get_settings
    from clipbench.models import Clip, Reference, Word

    audio_dir.mkdir(parents=True, exist_ok=True)
    verified_dir = get_settings().verified_dir
    verified_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    clips = []
    for i in range(N_CLIPS):
        vid = f"synth{i + 1:05d}"
        text, entities, alts, itn = _sentence(rng, i)
        toks = text.split()
        words = [Word(text=t, start_ms=300 + 420 * k, end_ms=300 + 420 * k + 360) for k, t in enumerate(toks)]
        speech_end = words[-1].end_ms
        conds = sorted({CONDITIONS[i % len(CONDITIONS)], CONDITIONS[(i * 3 + 1) % len(CONDITIONS)]}, key=CONDITIONS.index)
        music = "music" in conds
        duration = round((speech_end + (2500 if music else 600)) / 1000, 2)
        spans = [{"start_ms": speech_end + 200, "end_ms": int(duration * 1000), "kind": "music"}] if music else []
        if "multiple_speakers" in conds:
            mid = words[len(words) // 2].start_ms
            turns = [{"start_ms": 300, "end_ms": mid - 30, "speaker": "A"}, {"start_ms": mid, "end_ms": speech_end, "speaker": "B"}]
        else:
            turns = [{"start_ms": 300, "end_ms": speech_end, "speaker": "A"}]
        path = audio_dir / f"{vid}.wav"
        sha = _write_wav(path, duration, hz=180 + (i % 30) * 10)
        ref = Reference(
            video_id=vid, language=LANG, tier="verified", text=text, words=words, audio_sha256=sha,
            tags=(["background_music"] if music else []) + (["fast_speech"] if "fast_speech" in conds else []),
            category=CATEGORIES[i % len(CATEGORIES)], nonspeech_spans=spans, punctuated=i % 2 == 0,
            timing_source="creator", conditions=conds, speaker_turns=turns, entity_spans=entities,
            alternatives=alts, itn_spans=itn,
        )  # fmt: skip
        # The reviewer's verified file (synthetic): the publication gate's diversity check reads its category.
        (verified_dir / f"{vid}.json").write_text(json.dumps(ref.model_dump(mode="json")), encoding="utf-8")
        clips.append(Clip(video_id=vid, language=LANG, duration_s=duration, channel=f"synth-channel-{i % 40:02d}",
                          audio_path=str(path), audio_sha256=sha, reference=ref))  # fmt: skip
    return clips


# ---------------------------------------------------------------- providers


def _providers():
    from clipbench.models import Clip, Result, Word
    from clipbench.providers.base import Provider

    class Synth(Provider):
        id: ClassVar[str] = "synth"
        paid: ClassVar[bool] = True
        uploads_audio: ClassVar[bool] = True
        supported_options: ClassVar[frozenset[str]] = frozenset({"diarize", "detect_language", "keyterms"})
        # traits
        err: ClassVar[float] = 0.05
        music_mult: ClassVar[float] = 1.5
        offset_ms: ClassVar[int] = 60
        fillers: ClassVar[bool] = True
        punct: ClassVar[bool] = True
        itn: ClassVar[bool] = True
        diar: ClassVar[bool] = True
        lid_acc: ClassVar[float] = 0.95
        kt: ClassVar[bool] = True
        fail_every: ClassVar[int] = 0
        latency: ClassVar[float] = 900.0
        cpm: ClassVar[float] = 0.0043

        def __init__(self, model_id: str | None = None, **opts: Any) -> None:
            super().__init__(model_id or f"{self.id}-v1", **opts)

        def price_per_minute(self) -> float:
            return self.cpm if self.paid else 0.0

        async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
            assert clip is not None
            ref = clip.reference
            seed = int(hashlib.sha256(f"{self.id}:{self.model_id}:{clip.video_id}:{self.options.get('detect_language')}".encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            n = int(clip.video_id[-5:]) if clip.video_id[-5:].isdigit() else 0
            if self.fail_every and n % self.fail_every == 0:
                raise RuntimeError("synthetic failure")
            rate = self.err * (self.music_mult if "music" in ref.conditions else 1.0)
            rate *= 1.3 if "outdoor_noise" in ref.conditions or "whisper_shout" in ref.conditions else 1.0
            detected = None
            if self.detect_language:
                detected = "en" if rng.random() < self.lid_acc else "de"
                if detected != "en":
                    rate *= 2.5
            keep = set(self.keyterms) if (self.kt and self.keyterms) else set()
            out: list[Word] = []
            for w in ref.words or []:
                tok = w.text
                bare = re.sub(r"[.,!?]", "", tok)
                if bare.lower() == "um" and not self.fillers:
                    continue
                if not self.punct:
                    tok = bare.lower() if bare not in keep and bare not in KEYTERMS else bare
                if bare == "25" and not self.itn:
                    tok = tok.replace("25", "twenty five")
                if bare in KEYTERMS and bare not in keep and rng.random() < 0.45:
                    tok = tok.replace(bare, bare.lower()[:-1] + "s")
                r = rng.random()
                if r < rate * 0.3:
                    continue
                if r < rate:
                    tok = rng.choice(VOCAB)
                jitter = int(rng.gauss(0, self.offset_ms / 2))
                start = max(0, w.start_ms + self.offset_ms + jitter)
                for part in tok.split():
                    out.append(Word(text=part, start_ms=start, end_ms=start + (w.end_ms - w.start_ms)))
                    start += 120
            speakers = None
            if self.diarize and self.diar:
                speakers = []
                for w in out:
                    lab = next((t.speaker for t in ref.speaker_turns if t.start_ms - 200 <= w.start_ms <= t.end_ms + 200), None)
                    speakers.append(lab if rng.random() > 0.1 else ("B" if lab == "A" else "A"))
            res = Result(text=" ".join(w.text for w in out), words=out, latency_ms=self.latency * (0.7 + rng.random() * 0.6),
                         audio_seconds=clip.duration_s, cost_usd=self.estimate_cost(clip.duration_s), model_id=self.model_id,
                         provider_version="synth", speakers=speakers, detected_language=detected,
                         retries=1 if rng.random() < 0.04 else 0)  # fmt: skip
            return res

    def make(pid: str, **traits: Any):
        return type(pid.replace("-", "_"), (Synth,), {"id": pid, **traits})

    return [
        make("synth-a", err=0.04, music_mult=1.9, offset_ms=40, latency=800),
        make("synth-b", err=0.06, music_mult=1.3, offset_ms=180, fillers=False, diar=False, kt=False, cpm=0.0025, fail_every=37),
        make("synth-c", err=0.035, music_mult=1.2, offset_ms=90, itn=True, cpm=0.0067, latency=2100),
        make("synth-d", err=0.05, music_mult=1.6, offset_ms=140, punct=False, itn=False, kt=False, cpm=0.002, lid_acc=0.85),
        make("synth-e", err=0.07, music_mult=2.2, offset_ms=70, fillers=False, diar=False, cpm=0.00185, latency=600),
        make("synth-f", err=0.09, music_mult=2.4, offset_ms=230, paid=False, uploads_audio=False, diar=False, kt=False, lid_acc=0.8, latency=4800, cpm=0.0),
        # Below the coverage minimum (fails 1 clip in 6) but the lowest error rate: must never be ranked.
        make("synth-g", err=0.03, music_mult=1.2, offset_ms=80, fail_every=6, cpm=0.003, latency=700),
    ]


# ---------------------------------------------------------------- tracks


async def batch_runs(settings, clips) -> None:
    from clipbench.runner import run
    from clipbench.scoring.score import score_run

    classes = _providers()
    await run([c() for c in classes], "verified", clips=clips, settings=settings, run_id=BASE_RUN, progress=False,
              console=QUIET, stability_n=10, language=LANG)  # fmt: skip
    score_run(BASE_RUN, settings)
    await run([c() for c in classes], "verified", clips=clips, settings=settings, run_id=MAIN_RUN, progress=False,
              console=QUIET, stability_n=10, language=LANG,
              options={"diarize": True, "detect_language": True, "keyterms": KEYTERMS})  # fmt: skip
    score_run(MAIN_RUN, settings)
    # What `clipbench quality <run_id>` writes: the keyterms on/off comparison pairs entities across runs.
    from clipbench.scoring.entities import score_entities

    score_entities(BASE_RUN, settings)
    score_entities(MAIN_RUN, settings)


async def streaming(settings, tmp: Path) -> None:
    from clipbench.models import Clip, Reference, Word
    from clipbench.streaming.base import FakeClock
    from clipbench.streaming.fake import FakeStream
    from clipbench.streaming.fakeserver import memory_connect
    from clipbench.streaming.runner import run_stream
    from clipbench.streaming.score import score_stream

    rng = random.Random(11)
    clips, texts = [], {}
    for i in range(40):
        vid = f"synthst{i + 1:03d}"
        text = " ".join(rng.choice(VOCAB) for _ in range(rng.randint(5, 9)))
        words = [Word(text=t, start_ms=300 * k, end_ms=300 * k + 250) for k, t in enumerate(text.split())]
        wav = tmp / "stream" / f"{vid}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(wav, 1.0, value=(i + 1) * 300)
        conds = [CONDITIONS[i % 3]]
        ref = Reference(video_id=vid, language=LANG, tier="verified", text=text, words=words, conditions=conds,
                        category=CATEGORIES[i % len(CATEGORIES)], tags=["background_music"] if "music" in conds else [])  # fmt: skip
        clips.append(Clip(video_id=vid, language=LANG, duration_s=1.0, audio_path=str(wav), audio_sha256=f"sha-{vid}", reference=ref))
        texts[i] = text

    traits = {"synth-a": (0.05, 0.1), "synth-b": (0.08, 0.3), "synth-d": (0.06, 0.2), "synth-e": (0.1, 0.05)}

    def make(pid: str):
        class S(FakeStream):
            id: ClassVar[str] = pid

            @classmethod
            def default_model_id(cls) -> str:
                return f"{pid}-stream-1"

            def url(self, language: str) -> str:
                return f"memory://{pid}?language={language}"

        return S

    async def handler(server, url: str, headers: dict[str, str]) -> None:
        pid = url.split("//")[1].split("?")[0]
        err, flicker = traits[pid]
        idx, shown, tokens = None, 0, []
        r = random.Random(pid)
        while True:
            msg = await server.recv()
            if isinstance(msg, bytes):
                if idx is None and len(msg) >= 2:
                    idx = int(round(int(np.frombuffer(msg[:2], dtype="<i2")[0]) / 300)) - 1
                    tokens = [w if r.random() > err else r.choice(VOCAB) for w in texts.get(idx, "").split()]
                if shown < len(tokens):
                    shown += 1
                    partial = tokens[:shown]
                    if r.random() < flicker and partial:
                        partial = partial[:-1] + [r.choice(VOCAB)]
                    await server.send(json.dumps({"type": "partial", "seg": 0, "text": " ".join(partial)}))
                continue
            if json.loads(msg).get("type") == "end":
                words = [{"text": t, "start_ms": i * 300 + 40, "end_ms": i * 300 + 260} for i, t in enumerate(tokens)]
                await server.send(json.dumps({"type": "final", "seg": 0, "text": " ".join(tokens), "words": words}))
                await server.send(json.dumps({"type": "done"}))
                return

    connect, _ = memory_connect(handler)
    res = await run_stream([make(p)() for p in traits], "verified", clips=clips, settings=settings, connect=connect,
                           clock=FakeClock(), quiet_s=0.05, run_id="20261018-0900-stream", language=LANG, console=QUIET)  # fmt: skip
    score_stream(res.run_id, settings)


async def sweep(settings, tmp: Path) -> None:
    from clipbench.models import Clip, Reference, Result, Word
    from clipbench.providers.base import Provider
    from clipbench.sweeps import beds as beds_mod
    from clipbench.sweeps.build import build_sweep
    from clipbench.sweeps.run import run_sweep
    from clipbench.sweeps.score import score_sweep

    d = beds_mod.beds_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    t = np.arange(10 * SR) / SR
    chord = sum(0.1 * np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0)) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    sf.write(str(d / "synth-music.wav"), chord.astype(np.float32), SR, subtype="PCM_16")
    beds = [
        beds_mod.Bed(id="synth-music", kind="music", source="synthetic", license="CC0", commercial_use=True),
        beds_mod.Bed(id="synthetic-pink", kind="noise", source="synthetic", license="CC0", commercial_use=True,
                     generator={"type": "pink", "seconds": 20}),  # fmt: skip
    ]
    rng = random.Random(5)
    clips = []
    for i in range(16):
        vid = f"synthsw{i + 1:03d}"
        toks = [rng.choice(VOCAB) for _ in range(8)]
        words = [Word(text=w, start_ms=350 * k, end_ms=350 * k + 300) for k, w in enumerate(toks)]
        path = tmp / "sweep" / f"{vid}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        tt = np.arange(3 * SR) / SR
        x = 0.3 * np.sin(2 * np.pi * (200 + 20 * i) * tt) * ((tt % 0.6) < 0.4)
        sf.write(str(path), x.astype(np.float32), SR, subtype="PCM_16")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        ref = Reference(video_id=vid, language=LANG, tier="verified", text=" ".join(toks), words=words, audio_sha256=sha)
        clips.append(Clip(video_id=vid, language=LANG, duration_s=3.0, channel="c", audio_path=str(path), audio_sha256=sha, reference=ref))

    knees = {"synth-a": 4, "synth-b": 6, "synth-c": -2, "synth-d": 3, "synth-e": 9, "synth-f": 11}

    def make(pid: str, knee: float):
        class P(Provider):
            id: ClassVar[str] = pid
            paid: ClassVar[bool] = False
            uploads_audio: ClassVar[bool] = False

            def __init__(self) -> None:
                super().__init__(f"{pid}-v1")

            async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
                cond = clip.video_id.split("#")[1]
                m = re.search(r"snr(-?[\d.]+)", cond)
                snr = None if m is None else float(m.group(1))
                rate = 0.03 if snr is None else min(0.95, 0.03 + 0.6 / (1 + np.exp((snr - knee) / 2.5)))
                if cond.startswith("noise"):
                    rate *= 0.85
                r = random.Random(f"{pid}{clip.video_id}")
                words = [Word(text=w.text if r.random() > rate else "xx", start_ms=w.start_ms, end_ms=w.end_ms) for w in clip.reference.words]
                if snr is not None and snr <= knee - 4:
                    words.insert(0, Word(text="la", start_ms=500, end_ms=700))
                return Result(text=" ".join(w.text for w in words), words=words, latency_ms=10.0, audio_seconds=clip.duration_s,
                              cost_usd=0.0, model_id=self.model_id)  # fmt: skip

        return P

    build_sweep(LANG, [20, 10, 5, 0, -5], kinds=["music", "noise"], settings=settings, clips=clips, beds=beds,
                sweep_id="en-synth-sweep", log=lambda m: None)  # fmt: skip
    await run_sweep("en-synth-sweep", [make(p, k)() for p, k in knees.items()], settings=settings, progress=False, console=QUIET)
    score_sweep("en-synth-sweep", settings)


async def longform(settings, tmp: Path) -> None:
    from clipbench import longform as lf
    from clipbench.models import Clip, Reference, Result, Word
    from clipbench.providers.base import Provider

    def word(n: int) -> str:
        out = "q"
        for _ in range(3):
            out += "abcdefghijklmnoprstuvwyz"[n % 24]
            n //= 24
        return out

    clips = []
    for v in range(3):
        lines = [Word(text=" ".join(word(v * 1000 + i * 6 + k) for k in range(6)), start_ms=i * 15_000, end_ms=i * 15_000 + 4000) for i in range(48)]
        wav = tmp / "long" / f"lv{v}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(wav), (0.1 * np.random.default_rng(v).standard_normal(SR * 2)).astype(np.float32), SR)
        sha = hashlib.sha256(wav.read_bytes()).hexdigest()
        vid = f"synthlong{v:02d}"
        ref = Reference(video_id=vid, language=LANG, tier="creator", text=" ".join(x.text for x in lines), words=lines,
                        audio_sha256=sha, clip_source="long")  # fmt: skip
        clips.append(Clip(video_id=vid, language=LANG, duration_s=720.0, audio_path=str(wav), audio_sha256=sha, reference=ref))

    def make(pid: str, drift: float, dup: bool, err: float):
        class P(Provider):
            id: ClassVar[str] = pid
            paid: ClassVar[bool] = False
            uploads_audio: ClassVar[bool] = False

            def __init__(self) -> None:
                super().__init__(f"{pid}-v1")

            async def transcribe(self, audio_path: Path, language: str, *, clip: Clip | None = None) -> Result:
                r = random.Random(f"{pid}{clip.video_id}")
                out = []
                for line in clip.reference.words:
                    shift = int(drift * line.start_ms / 60_000)
                    for k, tok in enumerate(line.text.split()):
                        s = line.start_ms + shift + k * 600
                        out.append(Word(text=tok if r.random() > err else "qzzz", start_ms=s, end_ms=s + 500))
                if dup:
                    out = out[:60] + out[54:60] + out[60:]
                return Result(text=" ".join(w.text for w in out), words=out, latency_ms=20_000.0, audio_seconds=clip.duration_s,
                              cost_usd=0.0, model_id=self.model_id)  # fmt: skip

        return P

    provs = [make("synth-a", 5, False, 0.04)(), make("synth-c", 20, False, 0.03)(), make("synth-e", 90, True, 0.08)(), make("synth-f", 40, True, 0.1)()]
    res = await lf.run_longform(LANG, provs, clips=clips, settings=settings, run_id="lf-20261017-synth", progress=False)
    lf.score_longform(res.run_id if hasattr(res, "run_id") else "lf-20261017-synth", settings)


async def canaries(settings, clips) -> None:
    from clipbench import canary
    from clipbench.canary import Candidate, run_canary

    picked = [Candidate(clip_id=c.video_id, language=LANG, channel=c.channel) for c in clips[:30]]
    canary.write_canary(LANG, picked, 7, settings)
    canary_clips = clips[:30]
    classes = {c.id: c for c in _providers()}
    for week, day in enumerate([1, 8, 15, 22, 29]):
        when = datetime(2026, 9, day, 3, 17, tzinfo=UTC)
        provs = []
        for pid, cls in classes.items():
            # synth-e gets worse from the third rerun; synth-c ships a new model on the last one.
            if pid == "synth-e" and week >= 2:
                cls = type("synth_e_worse", (cls,), {"err": cls.err * 2.2})
            model = "synth-c-v2" if pid == "synth-c" and week == 4 else None
            provs.append(cls(model))
        await run_canary(LANG, provs, settings=settings, clips=canary_clips, console=QUIET, now=when)


def judge(settings) -> None:
    from clipbench.judge.run import calibrate, judge_run, labels_path

    def rule(ref: str, hyp: str) -> float:
        a, b = ref.lower().split(), hyp.lower().split()
        if not a:
            return 1.0
        same = sum(1 for x, y in zip(a, b, strict=False) if x == y) / len(a)
        return round(min(1.0, same) * 4) / 4

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        ref = user.split("Reference:\n")[1].split("\n\nHypothesis:")[0]
        hyp = user.split("Hypothesis:\n")[1]
        content = json.dumps({"score": rule(ref, hyp), "reason": "synthetic"})
        return httpx.Response(200, json={"success": True, "result": {"choices": [{"message": {"content": content}}],
                                                                      "usage": {"prompt_tokens": 400, "completion_tokens": 20}}})  # fmt: skip

    path = labels_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    rows = []
    for i in range(64):
        ref = " ".join(rng.choice(VOCAB) for _ in range(8))
        toks = ref.split()
        k = i % 5
        hyp = " ".join(toks[: 8 - k] + ["zz"] * k)
        rows.append({"lang": LANG, "ref": ref, "hyp": hyp, "human_score": rule(ref, hyp), "labeller": "synthetic",
                     "note": "", "pair_id": f"p{i}", "run_id": "synthetic", "provider": "p", "video_id": f"synthlab{i:03d}",
                     "labelled_at": "2026-10-10T00:00:00Z"})  # fmt: skip
    pd.DataFrame(rows).to_csv(path, index=False)
    transport = httpx.MockTransport(handler)
    calibrate(settings, transport=transport)
    judge_run(MAIN_RUN, settings, transport=transport)


def compute_bench(settings) -> None:
    from clipbench import compute

    bench = compute.bench_rows(
        ["synth-f"], [], LANG, bench_id="synth-bench",
        info={"chip": "Synthetic Chip", "cores_total": 12, "ram_bytes": 24 * 2**30, "os": "SynthOS", "python": "3.12"},
        worker=lambda p, c, lang: {"status": "ok", "model_id": "synth-f-v1", "backend": "mlx", "peak_rss_bytes": 3_900_000_000,
                                   "model_size_bytes": 1_550_000_000, "clips": [{"audio_s": 120.0, "wall_s": 9.0}]},
    )  # fmt: skip
    compute.append_bench(bench, settings)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cb-v3-") as d:
        tmp = Path(d)
        _env(tmp)
        from clipbench.config import get_settings
        from clipbench.report.export import export_run

        settings = get_settings()
        clips = make_clips(tmp / "audio")
        compute_bench(settings)
        asyncio.run(canaries(settings, clips))
        asyncio.run(batch_runs(settings, clips))
        asyncio.run(streaming(settings, tmp))
        asyncio.run(sweep(settings, tmp))
        asyncio.run(longform(settings, tmp))
        judge(settings)
        export_run(BASE_RUN, settings)
        run_path, index_path = export_run(MAIN_RUN, settings)
        data = run_path.parent.parent
        if OUT.exists():
            shutil.rmtree(OUT)
        (OUT / "runs").mkdir(parents=True)
        (OUT / "findings").mkdir()
        shutil.copy(index_path, OUT / "index.json")
        for p in (data / "runs").glob("*.json"):
            shutil.copy(p, OUT / "runs" / p.name)
        shutil.copy(data / "findings" / f"{LANG}.json", OUT / "findings" / f"{LANG}.json")
        run = json.loads(run_path.read_text())
        missing = [k for k in ("streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform",
                               "compute", "diarization", "lid", "entities", "style", "reliability", "captions", "features",
                               "run_meta") if run.get(k) is None]  # fmt: skip
        st = next(x for x in run["languages"] if x["lang"] == LANG)
        excluded = [e["provider"] for e in st["providers_excluded"]]
        problems = []
        if not st["published"]:
            problems.append(f"main run not published: gate={st['gate']} diversity={st['verified_diversity']}")
        if "synth-g" not in excluded:
            problems.append(f"synth-g is not excluded: eligible={st['providers_eligible']}")
        if problems:
            raise SystemExit("; ".join(problems))
        print("wrote", OUT, "| null sections:", missing or "none", "| eligible:", st["providers_eligible"], "| excluded:", excluded)


if __name__ == "__main__":
    main()
