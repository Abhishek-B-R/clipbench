"""Money guard: estimate before a run, refuse past the cap, reserve before every paid request.

`data/spend.json`:

    {"total_usd": 1.23, "by_provider": {"deepgram": 0.41},
     "events": [{"run_id": "...", "provider": "deepgram", "video_id": "...", "usd": 0.002, "at": "..."}]}

Only requests that actually went out are recorded. A cache hit costs nothing and is never written.

Several clipbench processes may share one ledger. Every read-modify-write holds an exclusive
`fcntl.flock` on `spend.json.lock` next to the ledger, and open reservations live in the ledger
itself (`"reserved": {<guard token>: {"usd", "pid"}}`) so a second process sees the money the first
one has promised. A reservation whose process is gone is ignored. Where `fcntl` is missing (Windows)
the lock is a no-op and only the in-process asyncio lock protects the ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipbench.models import Clip
from clipbench.providers.base import Provider
from clipbench.providers.cache import ResultCache

try:  # POSIX only; Windows falls back to the in-process lock
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None  # type: ignore[assignment]

BUDGET_ERROR = "budget"


class BudgetExceeded(Exception):
    """A run would cost more than the cap allows."""


def is_billable(provider: Provider) -> bool:
    """True when a request to this provider can cost money."""
    return bool(provider.paid) or provider.price_per_minute() > 0


def supports_language(provider: Provider, language: str | None) -> bool:
    """False only when the provider declares its languages (`languages` class attribute) and `language`
    is not one of them, e.g. cf-whisper-tiny-en on a Japanese run."""
    langs = getattr(provider, "languages", None)
    if not language or not langs:
        return True
    return language.split("-")[0].lower() in {str(x).split("-")[0].lower() for x in langs}


def stability_subset(clips: list[Clip], n: int, seed: int) -> list[Clip]:
    """The seeded random subset that paid providers rerun. Order independent of the input order."""
    if n <= 0 or not clips:
        return []
    ordered = sorted(clips, key=lambda c: c.video_id)
    if n >= len(ordered):
        return ordered
    rng = random.Random(seed)
    return sorted(rng.sample(ordered, n), key=lambda c: c.video_id)


def repeat_settings_hash(provider: Provider, repeat: int) -> str:
    """Cache key part for one stability repeat. Repeat 0 is the provider's normal settings hash."""
    if repeat == 0:
        return provider.settings_hash()
    blob = json.dumps({**provider.settings(), "_repeat": repeat}, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------- estimate


@dataclass
class ProviderEstimate:
    provider: str
    model_id: str
    n_clips: int
    audio_seconds: float
    main_usd: float
    stability_clips: int
    stability_repeats: int
    stability_usd: float
    cached_requests: int = 0

    @property
    def total_usd(self) -> float:
        return self.main_usd + self.stability_usd


@dataclass
class Estimate:
    providers: dict[str, ProviderEstimate] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return sum(p.total_usd for p in self.providers.values())

    @property
    def total_audio_seconds(self) -> float:
        return sum(p.audio_seconds for p in self.providers.values())


def estimate(
    providers: list[Provider],
    clips: list[Clip],
    *,
    stability_n: int = 20,
    stability_repeats: int = 3,
    seed: int = 7,
    cache: ResultCache | None = None,
) -> Estimate:
    """Upper bound of what running `providers` over `clips` costs.

    Includes the stability reruns (`stability_n` seeded clips x `stability_repeats`) for every paid
    provider. With `cache`, requests that would be served from the cache count as free.
    """
    out = Estimate()
    subset = stability_subset(clips, stability_n, seed)
    for p in providers:
        billable = is_billable(p)
        cached = 0
        main = 0.0
        audio = 0.0
        for c in clips:
            audio += c.duration_s
            if not billable:
                continue
            if cache is not None and cache.get(p.id, p.model_id, c.audio_sha256, p.settings_hash()):
                cached += 1
                continue
            main += p.estimate_cost(c.duration_s)
        stab = 0.0
        reps = stability_repeats if p.paid else 0
        stab_clips = len(subset) if p.paid else 0
        if billable and p.paid:
            for r in range(1, reps + 1):
                h = repeat_settings_hash(p, r)
                for c in subset:
                    if cache is not None and cache.get(p.id, p.model_id, c.audio_sha256, h):
                        cached += 1
                        continue
                    stab += p.estimate_cost(c.duration_s)
        out.providers[p.id] = ProviderEstimate(
            provider=p.id,
            model_id=p.model_id,
            n_clips=len(clips),
            audio_seconds=audio,
            main_usd=main,
            stability_clips=stab_clips,
            stability_repeats=reps,
            stability_usd=stab,
            cached_requests=cached,
        )
    return out


# ---------------------------------------------------------------- ledger


def _empty() -> dict[str, Any]:
    return {"total_usd": 0.0, "by_provider": {}, "events": []}


def load_spend(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty()
    base = _empty()
    base.update(data)
    return base


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    """Exclusive cross-process lock around a read-modify-write of the ledger at `path`.

    Blocks until the other holder is done. A no-op where fcntl is unavailable or the lock file cannot be
    opened (read-only data dir): the ledger is then only as safe as one process at a time."""
    if fcntl is None:
        yield
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path(path), "a+")  # noqa: SIM115 - held for the duration of the block
    except OSError:
        yield
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def update_spend(path: Path, change: Any) -> dict[str, Any]:
    """Load the ledger, apply `change(data)` (mutates in place) and write it back, all under the lock."""
    with ledger_lock(path):
        data = load_spend(path)
        change(data)
        _atomic_write_json(path, data)
        return data


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # exists but not ours, or the platform cannot tell: keep the reservation
    return True


def open_reservations(data: dict[str, Any], exclude: str | None = None) -> float:
    """USD reserved by live guards in the ledger, other than `exclude`."""
    total = 0.0
    for token, r in (data.get("reserved") or {}).items():
        if token == exclude or not isinstance(r, dict):
            continue
        if _pid_alive(r.get("pid")):
            total += float(r.get("usd") or 0.0)
    return total


def spent_usd(path: Path) -> float:
    return float(load_spend(path)["total_usd"])


def check_can_start(est: Estimate | float, cap: float, spent: float | None = None, *, path: Path | None = None) -> None:
    """Raise BudgetExceeded when estimate + spent > cap. `spent` defaults to the ledger at `path`."""
    total = est.total_usd if isinstance(est, Estimate) else float(est)
    if spent is None:
        spent = spent_usd(path) if path is not None else 0.0
    if total + spent > cap + 1e-12:
        raise BudgetExceeded(
            f"refusing to start: estimated ${total:.4f} + already spent ${spent:.4f} = ${total + spent:.4f}, "
            f"which is over the ${cap:.2f} cap. Raise budget_usd in clipbench.toml (or CLIPBENCH_BUDGET_USD), "
            "pick fewer providers, or use --limit."
        )


class BudgetGuard:
    """Mid-run guard shared by every provider task in one process, and safe across processes.

    reserve() before a paid request, then record() the actual cost (or release() when nothing was billed).
    Each call re-reads the ledger under the file lock, so spend and reservations written by another
    clipbench process count against the same cap.
    """

    def __init__(self, path: Path, cap: float, run_id: str) -> None:
        self.path = path
        self.cap = float(cap)
        self.run_id = run_id
        self.data = load_spend(path)
        self.reserved = 0.0
        self.token = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.lock = asyncio.Lock()

    @property
    def spent(self) -> float:
        return float(load_spend(self.path)["total_usd"]) if self.path.exists() else float(self.data["total_usd"])

    def _write_reservation(self, data: dict[str, Any]) -> None:
        res = {k: v for k, v in (data.get("reserved") or {}).items() if isinstance(v, dict) and _pid_alive(v.get("pid"))}
        if self.reserved > 1e-12:
            res[self.token] = {"usd": self.reserved, "pid": os.getpid(), "run_id": self.run_id}
        else:
            res.pop(self.token, None)
        if res:
            data["reserved"] = res
        else:
            data.pop("reserved", None)

    async def reserve(self, usd: float) -> bool:
        async with self.lock:
            with ledger_lock(self.path):
                data = load_spend(self.path)
                others = open_reservations(data, exclude=self.token)
                if float(data["total_usd"]) + others + self.reserved + usd > self.cap + 1e-12:
                    self.data = data
                    return False
                self.reserved += usd
                self._write_reservation(data)
                _atomic_write_json(self.path, data)
                self.data = data
                return True

    async def release(self, reserved_usd: float) -> None:
        async with self.lock:
            self.reserved = max(0.0, self.reserved - reserved_usd)
            with ledger_lock(self.path):
                data = load_spend(self.path)
                self._write_reservation(data)
                _atomic_write_json(self.path, data)
                self.data = data

    async def record(self, provider: str, video_id: str, usd: float, reserved_usd: float = 0.0) -> None:
        async with self.lock:
            self.reserved = max(0.0, self.reserved - reserved_usd)
            usd = float(usd)
            with ledger_lock(self.path):
                data = load_spend(self.path)
                data["total_usd"] = float(data["total_usd"]) + usd
                by = data.setdefault("by_provider", {})
                by[provider] = float(by.get(provider, 0.0)) + usd
                data.setdefault("events", []).append(
                    {
                        "run_id": self.run_id,
                        "provider": provider,
                        "video_id": video_id,
                        "usd": usd,
                        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
                self._write_reservation(data)
                _atomic_write_json(self.path, data)
                self.data = data
