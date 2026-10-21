"""Open harness (A11): validate a third-party adapter, run it on the public canary ids, bundle the numbers.

    clipbench submit --adapter path/to/module.py:Class --lang ja            # validate + dry-run price
    clipbench submit --adapter path/to/module.py:Class --lang ja --run      # real calls, your own keys
    clipbench submit --adapter path/to/module.py:Class --validate-only      # contract only, no network
    clipbench submission-check submissions/<name>                          # what CI runs on a PR

The contract (CONTRIBUTING.md) runs with the network blocked. It checks the class (a `Provider`
subclass with a new id), availability without keys, settings and pricing, the `Result` shape and word
timings on fake audio, and error handling. An adapter that calls an API gets the shape check offline by
defining `fixture_response` (a recorded response for a synthetic tone, no real speech) and
`parse_response(raw, audio_seconds) -> Result`, the same parser its `transcribe` uses.

A bundle (`submissions/<name>/`) holds numbers only: `submission.json` (run meta, contract report,
summary, the adapter's sha256), `rows.csv` (per canary clip: status, latency, cost, retries, lengths and a
sha256 of the hypothesis, never its text) and `adapter.py` (the exact file that was run). Error rates are
not in it: the verified references are private, so maintainers score the adapter by rerunning the
canary set before merging.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import importlib.util
import inspect
import json
import math
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import typer

from clipbench.config import Settings, get_settings
from clipbench.models import Clip, Reference, Result, Word
from clipbench.providers.base import Provider, ProviderSkip

SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FAKE_SECONDS = 2.0
CONTRACT_TIMEOUT_S = 15.0
DUMMY_KEY = "clipbench-contract-dummy-key"
ROW_FIELDS = [
    "clip_id", "status", "error_type", "latency_ms", "audio_seconds", "cost_usd", "retries", "timed_out",
    "http_status", "detected_language", "hyp_chars", "n_words", "hyp_sha256",
]  # fmt: skip
BUNDLE_FILES = {"submission.json", "rows.csv", "adapter.py"}
STATUSES = {"ok", "error", "skipped"}

Status = Literal["pass", "fail", "warn"]


# ---------------------------------------------------------------- network guard


class NetworkBlocked(OSError):
    """Raised for any connection attempt while the contract runs."""


@contextlib.contextmanager
def no_network() -> Iterator[list[str]]:
    """Block outgoing connections and DNS for the duration. Yields the list of attempts made."""
    attempts: list[str] = []
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def deny(what: str):
        def _deny(*args: Any, **kwargs: Any):
            attempts.append(what)
            raise NetworkBlocked(f"network is blocked during contract validation ({what})")

        return _deny

    socket.socket.connect = deny("connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = deny("connect_ex")  # type: ignore[method-assign]
    socket.create_connection = deny("create_connection")  # type: ignore[assignment]
    socket.getaddrinfo = deny("getaddrinfo")  # type: ignore[assignment]
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved  # type: ignore[method-assign,assignment]


@contextlib.contextmanager
def env_without(keys: tuple[str, ...], fill: str | None = None) -> Iterator[None]:
    """Unset `keys` (or set them to `fill`) for the duration."""
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            if fill is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = fill
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------- loading


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_spec(spec: str) -> tuple[Path, str]:
    if ":" not in spec:
        raise typer.BadParameter("--adapter must be path/to/module.py:ClassName")
    path, _, name = spec.rpartition(":")
    if not path.endswith(".py") or not name.isidentifier():
        raise typer.BadParameter("--adapter must be path/to/module.py:ClassName")
    return Path(path), name


def load_adapter(path: Path, class_name: str) -> type[Provider]:
    """Import the adapter file as its own module (network blocked) and return the class."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"adapter file not found: {path}")
    mod_name = f"clipbench_submission_{file_sha256(path)[:12]}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    # Never write __pycache__ next to a submitted adapter: inside a bundle it would be an unexpected file,
    # and check_bundle would stop there before it ever compares adapter_sha256.
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with no_network() as attempts:
            spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = write_bytecode
    if attempts:
        raise ImportError(f"importing {path.name} tried to use the network ({', '.join(attempts)})")
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"{path.name} has no class {class_name}")
    return cls


# ---------------------------------------------------------------- contract


@dataclass
class Check:
    name: str
    status: Status
    detail: str = ""


@dataclass
class ContractReport:
    provider_id: str | None
    model_id: str | None
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def add(self, name: str, status: Status, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def fake_audio(directory: Path, seconds: float = FAKE_SECONDS) -> Path:
    """A synthetic 16 kHz mono tone. Not speech, so no caption text can come out of it."""
    import soundfile as sf

    rate = 16_000
    t = np.arange(int(seconds * rate)) / rate
    tone = (0.2 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    path = Path(directory) / "contract.wav"
    sf.write(str(path), tone, rate, subtype="PCM_16")
    return path


def fake_clip(audio: Path, language: str, seconds: float = FAKE_SECONDS) -> Clip:
    sha = file_sha256(audio)
    ref = Reference(video_id="contract0001", language=language, tier="verified", text="", audio_sha256=sha)
    return Clip(video_id="contract0001", language=language, duration_s=seconds, channel=None,
                audio_path=str(audio), audio_sha256=sha, reference=ref)  # fmt: skip


def _finite(x: Any, *, positive: bool = False) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and (f > 0 if positive else f >= 0)


def result_problems(result: Any, seconds: float) -> tuple[list[str], list[str]]:
    """(problems, warnings) of a Result returned for `seconds` of audio."""
    if not isinstance(result, Result):
        return [f"returned {type(result).__name__}, not clipbench.models.Result"], []
    bad: list[str] = []
    warn: list[str] = []
    if not isinstance(result.text, str):
        bad.append("text is not a string")
    if not result.model_id:
        bad.append("model_id is empty")
    if not _finite(result.latency_ms):
        bad.append("latency_ms must be a finite number >= 0")
    if not _finite(result.audio_seconds, positive=True):
        bad.append("audio_seconds must be > 0")
    elif abs(float(result.audio_seconds) - seconds) > max(1.0, 0.1 * seconds):
        bad.append(f"audio_seconds {result.audio_seconds} is far from the {seconds} s of audio sent")
    if not _finite(result.cost_usd):
        bad.append("cost_usd must be a finite number >= 0")
    if result.retries is None or int(result.retries) < 0:
        bad.append("retries must be an int >= 0")
    if result.http_status is not None and not (100 <= int(result.http_status) <= 599):
        bad.append("http_status must be an HTTP status code or null")
    if result.detected_language is not None and not isinstance(result.detected_language, str):
        bad.append("detected_language must be a string or null")
    if result.words is None:
        warn.append("no word timings (words is null): timing metrics will be empty for this provider")
    else:
        last = -1
        limit = seconds * 1000 + 1000
        for i, w in enumerate(result.words):
            if not isinstance(w, Word):
                bad.append(f"words[{i}] is not a Word")
                break
            if w.start_ms < 0 or w.end_ms < w.start_ms:
                bad.append(f"words[{i}] has start_ms {w.start_ms} and end_ms {w.end_ms}")
                break
            if w.end_ms > limit:
                bad.append(f"words[{i}] ends at {w.end_ms} ms, after the end of the audio")
                break
            if w.start_ms < last:
                bad.append(f"words[{i}] starts before the previous word (timings must be in order)")
                break
            last = w.start_ms
        if result.speakers is not None and len(result.speakers) != len(result.words):
            bad.append("speakers must be parallel to words (same length)")
    if result.words is None and result.speakers is not None:
        bad.append("speakers without words")
    try:
        json.dumps(result.raw_response, default=str)
    except (TypeError, ValueError) as exc:
        bad.append(f"raw_response is not JSON serialisable ({exc})")
    return bad, warn


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _call(fn: Any, *args: Any, timeout: float = CONTRACT_TIMEOUT_S, **kwargs: Any) -> Any:
    return await asyncio.wait_for(_maybe_await(fn(*args, **kwargs)), timeout=timeout)


async def validate_adapter(cls: Any, language: str = "en") -> ContractReport:
    """Run the contract on an adapter class with the network blocked."""
    pid = getattr(cls, "id", None)
    report = ContractReport(provider_id=pid if isinstance(pid, str) else None, model_id=None)
    if not (inspect.isclass(cls) and issubclass(cls, Provider)):
        report.add("class", "fail", "the adapter must subclass clipbench.providers.base.Provider")
        return report
    if inspect.isabstract(cls):
        report.add("class", "fail", "the adapter is abstract (implement transcribe)")
        return report
    report.add("class", "pass", f"{cls.__name__} subclasses Provider")

    from clipbench.providers.registry import REGISTRY

    if not isinstance(pid, str) or not ID_RE.match(pid):
        report.add("id", "fail", "id must match [a-z0-9][a-z0-9._-]{0,63}")
    elif pid in REGISTRY:
        report.add("id", "fail", f"id {pid!r} is already a clipbench provider; pick a new one")
    else:
        report.add("id", "pass", pid)

    attr_bad = []
    if not isinstance(cls.paid, bool):
        attr_bad.append("paid must be a bool")
    if not isinstance(cls.uploads_audio, bool):
        attr_bad.append("uploads_audio must be a bool")
    if not isinstance(cls.concurrency, int) or not 1 <= cls.concurrency <= 64:
        attr_bad.append("concurrency must be an int from 1 to 64")
    if not _finite(cls.timeout_s, positive=True):
        attr_bad.append("timeout_s must be > 0")
    if not isinstance(cls.env_keys, tuple) or not all(isinstance(k, str) for k in cls.env_keys):
        attr_bad.append("env_keys must be a tuple of environment variable names")
    report.add("attributes", "fail" if attr_bad else "pass", "; ".join(attr_bad))
    env_keys: tuple[str, ...] = tuple(cls.env_keys) if isinstance(cls.env_keys, tuple) else ()

    with no_network() as attempts, env_without(env_keys):
        try:
            inst = cls()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.add("construct", "fail", f"{cls.__name__}() raised {type(exc).__name__}: {exc}")
            return report
        report.model_id = inst.model_id if isinstance(inst.model_id, str) else None
        report.add("construct", "pass" if report.model_id else "fail",
                   f"model_id {inst.model_id!r}" if report.model_id else "model_id must be a non-empty string")  # fmt: skip

        try:
            avail = inst.availability()
            if not (isinstance(avail, tuple) and len(avail) == 2 and isinstance(avail[0], bool)
                    and isinstance(avail[1], str)):  # fmt: skip
                report.add("availability_without_keys", "fail", "availability() must return (bool, str)")
            elif env_keys and avail[0]:
                report.add("availability_without_keys", "fail",
                           f"availability() says ok with {', '.join(env_keys)} unset")  # fmt: skip
            else:
                report.add("availability_without_keys", "pass", avail[1])
        except Exception as exc:  # noqa: BLE001
            report.add("availability_without_keys", "fail", f"availability() raised {type(exc).__name__}: {exc}")

        try:
            settings = inst.settings()
            json.dumps(settings, sort_keys=True)
            same = inst.settings_hash() == inst.settings_hash()
            report.add("settings", "pass" if same else "fail", "" if same else "settings_hash() is not stable")
        except Exception as exc:  # noqa: BLE001
            report.add("settings", "fail", f"settings() must be JSON serialisable ({type(exc).__name__}: {exc})")

        try:
            price = float(inst.price_per_minute())
            cost = float(inst.estimate_cost(60.0))
            if not (math.isfinite(price) and math.isfinite(cost) and price >= 0 and cost >= 0):
                report.add("pricing", "fail", "price_per_minute() and estimate_cost() must be finite and >= 0")
            elif cls.paid and price <= 0:
                report.add("pricing", "fail", "a paid adapter needs price_per_minute() > 0 so the budget guard works")
            else:
                report.add("pricing", "pass", f"${price:.5f} per minute")
        except Exception as exc:  # noqa: BLE001
            report.add("pricing", "fail", f"pricing raised {type(exc).__name__}: {exc}")

        with tempfile.TemporaryDirectory(prefix="clipbench-contract-") as tmp:
            audio = fake_audio(Path(tmp))
            clip = fake_clip(audio, language)
            parse = getattr(inst, "parse_response", None)
            fixture = getattr(inst, "fixture_response", None)
            if callable(parse) and fixture is not None:
                await _shape_from_fixture(report, parse, fixture)
                await _malformed(report, parse)
            pre = len(attempts)
            if pre:
                report.add("no_network", "fail", f"the adapter tried the network {pre} times outside transcribe()")
            else:
                report.add("no_network", "pass", "no connection attempts outside transcribe()")
            await _offline_transcribe(report, inst, audio, clip, language, env_keys, attempts,
                                      has_fixture=callable(parse) and fixture is not None)  # fmt: skip
    return report


async def _shape_from_fixture(report: ContractReport, parse: Any, fixture: Any) -> None:
    try:
        result = await _call(parse, fixture, FAKE_SECONDS)
    except Exception as exc:  # noqa: BLE001
        report.add("result_shape", "fail", f"parse_response(fixture_response) raised {type(exc).__name__}: {exc}")
        return
    bad, warn = result_problems(result, FAKE_SECONDS)
    report.add("result_shape", "fail" if bad else "pass", "; ".join(bad) or "Result from fixture_response is valid")
    report.add("word_timings", "warn" if warn else ("fail" if bad else "pass"),
               "; ".join(warn) or ("see result_shape" if bad else "word timings are in order and inside the audio"))  # fmt: skip


async def _malformed(report: ContractReport, parse: Any) -> None:
    returned = []
    for bad in ({}, None, {"unexpected": True}):
        try:
            out = await _call(parse, bad, FAKE_SECONDS)
        except Exception:  # noqa: BLE001 - raising is the wanted behaviour
            continue
        if isinstance(out, Result):
            returned.append(repr(bad))
    if returned:
        report.add("error_handling_malformed", "fail",
                   f"parse_response returned a Result for a malformed response ({', '.join(returned)}); raise instead")  # fmt: skip
    else:
        report.add("error_handling_malformed", "pass", "malformed responses raise")


async def _offline_transcribe(report: ContractReport, inst: Provider, audio: Path, clip: Clip, language: str,
                              env_keys: tuple[str, ...], attempts: list[str], *, has_fixture: bool) -> None:  # fmt: skip
    """transcribe() with dummy keys and no network: must fail cleanly, or (a local model) return a Result."""
    before = len(attempts)
    with env_without(env_keys, fill=DUMMY_KEY):
        t0 = time.perf_counter()
        try:
            result = await _call(inst.transcribe, audio, language, clip=clip, timeout=CONTRACT_TIMEOUT_S)
        except TimeoutError:
            report.add("error_handling_network", "fail",
                       f"transcribe() did not finish within {CONTRACT_TIMEOUT_S:.0f} s with the network down")  # fmt: skip
            return
        except ProviderSkip:
            report.add("error_handling_network", "fail",
                       "transcribe() raised ProviderSkip for a network failure; raise an error so it is counted")  # fmt: skip
            return
        except Exception as exc:  # noqa: BLE001 - the expected path for an API adapter
            used = len(attempts) - before
            report.add("error_handling_network", "pass",
                       f"raised {type(exc).__name__} after {time.perf_counter() - t0:.1f} s ({used} blocked attempts)")  # fmt: skip
            if not has_fixture:
                report.add("result_shape", "fail",
                           "no offline Result to check: define fixture_response and parse_response(raw, audio_seconds)")  # fmt: skip
            return
    if len(attempts) > before:
        report.add("error_handling_network", "fail", "transcribe() returned a Result although the network was down")
        return
    report.add("error_handling_network", "pass", "transcribe() works offline (local model)")
    if not has_fixture:
        bad, warn = result_problems(result, FAKE_SECONDS)
        report.add("result_shape", "fail" if bad else "pass", "; ".join(bad) or "offline Result is valid")
        report.add("word_timings", "warn" if warn else ("fail" if bad else "pass"), "; ".join(warn))


def validate_file(path: Path, class_name: str, language: str = "en") -> ContractReport:
    try:
        cls = load_adapter(path, class_name)
    except Exception as exc:  # noqa: BLE001 - reported
        report = ContractReport(None, None)
        report.add("import", "fail", f"{type(exc).__name__}: {exc}")
        return report
    report = asyncio.run(validate_adapter(cls, language))
    report.checks.insert(0, Check("import", "pass", f"{Path(path).name}:{class_name}"))
    return report


# ---------------------------------------------------------------- run on the canary ids


@dataclass
class Plan:
    clips: list[Clip]
    missing: list[str]
    estimate_usd: float
    audio_seconds: float


def submissions_dir(settings: Settings) -> Path:
    env = os.environ.get("CLIPBENCH_SUBMISSIONS_DIR")
    return Path(env) if env else settings.root / "submissions"


def plan(provider: Provider, lang: str, settings: Settings) -> Plan:
    """The canary clips whose audio is on this machine, and what running them would cost."""
    import soundfile

    from clipbench.canary import load_canary

    ids = load_canary(lang, settings)
    if not ids:
        raise FileNotFoundError(
            f"no canary set for {lang} yet (datasets/canary/{lang}.csv); submissions run on it once it is frozen"
        )
    clips, missing = [], []
    for cid in ids:
        wav = settings.audio_dir / f"{cid}.wav"
        if not wav.is_file():
            missing.append(cid)
            continue
        seconds = float(soundfile.info(str(wav)).duration)
        sha = file_sha256(wav)
        ref = Reference(video_id=cid, language=lang, tier="verified", text="", audio_sha256=sha)
        clips.append(Clip(video_id=cid, language=lang, duration_s=seconds, audio_path=str(wav),
                          audio_sha256=sha, reference=ref))  # fmt: skip
    cost = sum(provider.estimate_cost(c.duration_s) for c in clips)
    return Plan(clips, missing, cost, sum(c.duration_s for c in clips))


async def run_submission(provider: Provider, clips: list[Clip], lang: str, max_usd: float) -> list[dict[str, Any]]:
    """One request per clip. Errors are rows; the run stops paying once the next request would pass max_usd."""
    sem = asyncio.Semaphore(max(1, int(provider.concurrency)))
    lock = asyncio.Lock()
    spent = {"usd": 0.0, "reserved": 0.0}
    rows: list[dict[str, Any]] = []

    async def one(c: Clip) -> None:
        row: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
        row.update(clip_id=c.video_id, status="ok", audio_seconds=c.duration_s, cost_usd=0.0, retries=0,
                   timed_out=False)  # fmt: skip
        est = provider.estimate_cost(c.duration_s)
        async with sem:
            async with lock:
                if spent["usd"] + spent["reserved"] + est > max_usd + 1e-12:
                    row.update(status="skipped", error_type="budget")
                    rows.append(row)
                    return
                spent["reserved"] += est
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(provider.transcribe(Path(c.audio_path), lang, clip=c),
                                                timeout=float(provider.job_timeout_s()))  # fmt: skip
            except ProviderSkip:
                row.update(status="skipped", error_type="ProviderSkip")
                cost = 0.0
            except TimeoutError:
                row.update(status="error", error_type="timeout", timed_out=True,
                           latency_ms=(time.perf_counter() - t0) * 1000)  # fmt: skip
                cost = est  # may have been billed
            except Exception as exc:  # noqa: BLE001 - every failure is a row
                # Only the exception type: messages can echo response bodies.
                row.update(status="error", error_type=type(exc).__name__,
                           http_status=getattr(exc, "http_status", None) or getattr(exc, "status_code", None),
                           retries=int(getattr(exc, "retries", 0) or 0),
                           latency_ms=(time.perf_counter() - t0) * 1000)  # fmt: skip
                cost = 0.0
            else:
                cost = float(result.cost_usd)
                row.update(
                    latency_ms=float(result.latency_ms), audio_seconds=float(result.audio_seconds),
                    cost_usd=cost, retries=int(result.retries or 0), http_status=result.http_status,
                    detected_language=result.detected_language, hyp_chars=len(result.text),
                    n_words=None if result.words is None else len(result.words),
                    hyp_sha256=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
                )  # fmt: skip
            async with lock:
                spent["reserved"] -= est
                spent["usd"] += cost
        rows.append(row)

    await asyncio.gather(*(one(c) for c in clips))
    return sorted(rows, key=lambda r: r["clip_id"])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["status"] == "ok"]
    req = [r for r in rows if r["status"] in ("ok", "error")]
    lat = [float(r["latency_ms"]) for r in ok if r.get("latency_ms") not in (None, "")]

    def q(p: float) -> float | None:
        return round(float(np.quantile(lat, p)), 3) if lat else None

    def flag(v: Any) -> bool:
        return v is True or str(v).lower() == "true"

    return {
        "n_clips": len(rows),
        "n_ok": len(ok),
        "n_errors": sum(r["status"] == "error" for r in rows),
        "n_skipped": sum(r["status"] == "skipped" for r in rows),
        "n_timeouts": sum(flag(r.get("timed_out")) for r in rows),
        "cost_usd_total": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
        "audio_seconds_total": round(sum(float(r.get("audio_seconds") or 0) for r in ok), 3),
        "failure_rate": round(sum(r["status"] == "error" for r in req) / len(req), 6) if req else None,
        "latency_p50": q(0.5),
        "latency_p95": q(0.95),
        "with_word_timings": sum(1 for r in ok if r.get("n_words") not in (None, "")),
    }


def _git_commit(root: Path) -> str | None:
    import subprocess

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def write_bundle(name: str, adapter_path: Path, class_name: str, provider: Provider, lang: str,
                 rows: list[dict[str, Any]], report: ContractReport, n_missing: int, settings: Settings) -> Path:  # fmt: skip
    from clipbench import __version__
    from clipbench.canary import canary_sha256

    out = submissions_dir(settings) / name
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(adapter_path, out / "adapter.py")
    with (out / "rows.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ROW_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r[k] for k in ROW_FIELDS})
    meta = {
        "schema": SCHEMA_VERSION,
        "name": name,
        "provider_id": provider.id,
        "model_id": provider.model_id,
        "class_name": class_name,
        "adapter_file": "adapter.py",
        "adapter_sha256": file_sha256(out / "adapter.py"),
        "language": lang,
        "canary_sha256": canary_sha256(lang, settings),
        "canary_clips_missing_audio": n_missing,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clipbench_version": __version__,
        "clipbench_commit": _git_commit(settings.root),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "settings": provider.settings(),
        "paid": bool(provider.paid),
        "uploads_audio": bool(provider.uploads_audio),
        "usd_per_minute": float(provider.price_per_minute()),
        "contract": report.as_dict(),
        "summary": summarize(rows),
        "scored": False,
        "note": "numbers only; error rates come from the maintainers' rerun on the private verified references",
    }
    (out / "submission.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str) + "\n")
    return out


# ---------------------------------------------------------------- bundle check (CI)


def check_bundle(directory: Path, settings: Settings | None = None) -> list[str]:
    """Problems with a submission bundle; empty when it is fine. Runs the contract offline on adapter.py."""
    from clipbench.canary import canary_sha256, load_canary

    settings = settings or get_settings()
    d = Path(directory)
    problems: list[str] = []
    files = {p.name for p in d.iterdir()} if d.is_dir() else set()
    if not d.is_dir():
        return [f"{d} is not a directory"]
    if missing := BUNDLE_FILES - files:
        problems.append(f"missing {', '.join(sorted(missing))}")
    if extra := files - BUNDLE_FILES - {"README.md"}:
        problems.append(f"unexpected files {', '.join(sorted(extra))} (a bundle holds numbers only)")
    if problems:
        return problems
    try:
        meta = json.loads((d / "submission.json").read_text())
    except ValueError as exc:
        return [f"submission.json is not JSON ({exc})"]
    for key in ("schema", "name", "provider_id", "model_id", "class_name", "adapter_sha256", "language",
                "contract", "summary"):  # fmt: skip
        if key not in meta:
            problems.append(f"submission.json has no {key}")
    if problems:
        return problems
    if meta["schema"] != SCHEMA_VERSION:
        problems.append(f"schema {meta['schema']} is not {SCHEMA_VERSION}")
    if not NAME_RE.match(str(meta["name"])) or meta["name"] != d.name:
        problems.append("name must match the directory name and [a-z0-9][a-z0-9._-]*")
    if file_sha256(d / "adapter.py") != meta["adapter_sha256"]:
        problems.append("adapter.py does not match adapter_sha256 (the bundle must hold the adapter that ran)")
    if meta.get("scored"):
        problems.append("scored must be false: error rates come from the maintainers' rerun, never a submitter")
    for key, value in _strings(meta):
        if len(value) > 400:
            problems.append(f"submission.json {key} is a long text field; bundles hold numbers, never captions")

    with (d / "rows.csv").open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ROW_FIELDS:
            problems.append(f"rows.csv columns must be exactly {', '.join(ROW_FIELDS)}")
            return problems
        rows = list(reader)
    canary = set(load_canary(str(meta["language"]), settings))
    for i, r in enumerate(rows, start=2):
        if r["status"] not in STATUSES:
            problems.append(f"rows.csv line {i}: status {r['status']!r}")
        if canary and r["clip_id"] not in canary:
            problems.append(f"rows.csv line {i}: {r['clip_id']} is not in the {meta['language']} canary set")
        if r["hyp_sha256"] and not re.fullmatch(r"[0-9a-f]{64}", r["hyp_sha256"]):
            problems.append(f"rows.csv line {i}: hyp_sha256 must be a sha256 hex digest")
        for col in ("latency_ms", "audio_seconds", "cost_usd", "retries", "http_status", "hyp_chars", "n_words"):
            if r[col] and not _finite(r[col]):
                problems.append(f"rows.csv line {i}: {col} {r[col]!r} is not a number >= 0")
        if r["error_type"] and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", r["error_type"]):
            problems.append(f"rows.csv line {i}: error_type must be an exception name, not a message")
    if len({r["clip_id"] for r in rows}) != len(rows):
        problems.append("rows.csv has a clip twice")
    if canary and meta.get("canary_sha256") != canary_sha256(str(meta["language"]), settings):
        problems.append("canary_sha256 does not match the current canary set; rerun on the current set")
    recomputed = summarize(rows)
    for key, value in recomputed.items():
        if meta["summary"].get(key) != value:
            problems.append(f"summary.{key} is {meta['summary'].get(key)!r} but rows.csv gives {value!r}")
    report = validate_file(d / "adapter.py", str(meta["class_name"]), str(meta["language"]))
    for c in report.checks:
        if c.status == "fail":
            problems.append(f"contract {c.name}: {c.detail}")
    if report.provider_id and report.provider_id != meta["provider_id"]:
        problems.append(f"provider_id {meta['provider_id']!r} is not the adapter's id {report.provider_id!r}")
    return problems


def _strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")


# ---------------------------------------------------------------- CLI


def _print_report(report: ContractReport) -> None:
    for c in report.checks:
        typer.echo(f"  {c.status:4}  {c.name}{': ' + c.detail if c.detail else ''}")
    typer.echo("contract: " + ("passed" if report.ok else "FAILED"))


def register(app: typer.Typer) -> None:
    @app.command("submit")
    def submit_cmd(
        adapter: str = typer.Option(..., "--adapter", help="path/to/module.py:ClassName"),
        lang: str = typer.Option("en", "--lang", help="Canary language to run on."),
        name: str | None = typer.Option(None, "--name", help="Bundle name (default: the adapter id)."),
        validate_only: bool = typer.Option(False, "--validate-only", help="Contract only, no network."),
        do_run: bool = typer.Option(False, "--run", help="Make the real calls with your own keys."),
        max_usd: float = typer.Option(1.0, "--max-usd", help="Hard cap for this submission run."),
    ) -> None:
        """Validate an adapter, price it on the public canary ids, and (with --run) write a bundle."""
        path, class_name = parse_spec(adapter)
        report = validate_file(path, class_name, lang)
        typer.echo(f"adapter {path.name}:{class_name} sha256 {file_sha256(path) if path.is_file() else '-'}")
        _print_report(report)
        if not report.ok:
            raise typer.Exit(1)
        if validate_only:
            return
        settings = get_settings()
        provider = load_adapter(path, class_name)()
        try:
            p = plan(provider, lang, settings)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(
            f"canary {lang}: {len(p.clips)} clips with audio here, {len(p.missing)} missing, "
            f"{p.audio_seconds / 60:.1f} audio min, estimated ${p.estimate_usd:.4f} (cap --max-usd ${max_usd:.2f})"
        )
        if p.missing:
            typer.echo(f"missing audio for {len(p.missing)} canary clips under data/audio/ (fetch them first)")
        if not do_run:
            typer.echo("dry run: no request was made. Add --run to call the provider with your own keys.")
            return
        ok, message = provider.availability()
        if not ok:
            typer.echo(message, err=True)
            raise typer.Exit(1)
        if not p.clips:
            typer.echo("no canary audio on this machine", err=True)
            raise typer.Exit(1)
        if p.estimate_usd > max_usd + 1e-12:
            typer.echo(f"refusing: estimate ${p.estimate_usd:.4f} is over --max-usd ${max_usd:.2f}", err=True)
            raise typer.Exit(2)
        rows = asyncio.run(run_submission(provider, p.clips, lang, max_usd))
        out = write_bundle(name or provider.id, path, class_name, provider, lang, rows, report, len(p.missing),
                           settings)  # fmt: skip
        problems = check_bundle(out, settings)
        for msg in problems:
            typer.echo(f"bundle problem: {msg}", err=True)
        typer.echo(f"wrote {out} ({summarize(rows)['n_ok']} ok of {len(rows)}); open a PR with that directory")

    @app.command("submission-check")
    def check_cmd(
        directory: Path | None = typer.Argument(None, help="submissions/<name>"),
        all_: bool = typer.Option(False, "--all", help="Check every bundle under submissions/."),
    ) -> None:
        """Validate submission bundles (numbers only, hashes, contract offline). What CI runs on PRs."""
        settings = get_settings()
        dirs = sorted(p for p in submissions_dir(settings).glob("*") if p.is_dir()) if all_ else []
        if directory is not None:
            dirs.append(directory)
        if not dirs:
            typer.echo("no submission bundles to check")
            return
        failed = False
        for d in dirs:
            problems = check_bundle(d, settings)
            typer.echo(f"{d}: {'ok' if not problems else 'FAILED'}")
            for msg in problems:
                typer.echo(f"  {msg}")
            failed = failed or bool(problems)
        if failed:
            raise typer.Exit(1)
