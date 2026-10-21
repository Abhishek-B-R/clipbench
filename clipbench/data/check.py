"""Diversity rules (docs/V2.md), enforced by `clipbench data check --lang L`.

Per language set: at least 30 channels, no channel above 5% of the set, at least 6 categories present
(not counting `other`). The set is the language's rows of data/sample.parquet by default, or the
eligible manifest pool. Guessed categories count unless `strict`.

`check_verified` applies the same rules to the verified clips only, with categories read from the verified
files (never guesses). The publication gate (clipbench.report.rules) and `clipbench data check --verified`
use it. Only metadata keys of a verified file are read here, never its text beyond an emptiness check.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

MIN_CHANNELS = 30
MAX_CHANNEL_SHARE = 0.05
MIN_CATEGORIES = 6


@dataclass
class CheckResult:
    ok: bool
    n_clips: int
    n_channels: int
    top_channel: str | None
    top_share: float
    categories: dict[str, int]
    guessed: int
    failures: list[str] = field(default_factory=list)

    def report(self, language: str, what: str) -> str:
        lines = [
            f"[{language}] {what}: {self.n_clips} clips, {self.n_channels} channels, "
            f"largest channel {self.top_share:.1%} ({self.top_channel})",
            "  categories: "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.categories.items())) or "none")
            + f" ({self.guessed} guessed)",
        ]
        lines += [f"  FAIL: {f}" for f in self.failures]
        lines.append("  OK" if self.ok else "  check failed")
        return "\n".join(lines)


def check_set(df: pd.DataFrame, *, strict: bool = False) -> CheckResult:
    n = len(df)
    chan = df["channel_id"].fillna(df.get("channel")).fillna("<no channel>") if n else pd.Series(dtype=str)
    counts = chan.value_counts()
    top = counts.index[0] if len(counts) else None
    top_share = float(counts.iloc[0] / n) if n else 0.0
    cat = df["category"] if "category" in df else pd.Series([None] * n, index=df.index)
    guess = (
        df["category_guess"].fillna(False).astype(bool)
        if "category_guess" in df
        else pd.Series(False, index=df.index)
    )
    used = cat.where(~guess, None) if strict else cat
    cats = Counter(c for c in used if isinstance(c, str) and c)
    real = [c for c in cats if c != "other"]
    failures = []
    if counts.size < MIN_CHANNELS:
        failures.append(f"{counts.size} channels, need at least {MIN_CHANNELS}")
    if n and top_share > MAX_CHANNEL_SHARE:
        failures.append(f"channel {top} is {top_share:.1%} of the set, max {MAX_CHANNEL_SHARE:.0%}")
    if len(real) < MIN_CATEGORIES:
        failures.append(f"{len(real)} categories (not counting other), need at least {MIN_CATEGORIES}")
    if n == 0:
        failures.append("empty set")
    return CheckResult(
        ok=not failures,
        n_clips=n,
        n_channels=int(counts.size),
        top_channel=top,
        top_share=top_share,
        categories=dict(cats),
        guessed=int((guess & cat.notna()).sum()),
        failures=failures,
    )


# ---------------------------------------------------------------- the verified set (publication gate)


def _verified_file(video_id: str, verified_dir: Path) -> dict | None:
    """The reviewer's verified file, or None when missing or unreadable. Only metadata keys are used."""
    path = verified_dir / f"{video_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def verified_category(video_id: str, verified_dir: Path) -> str | None:
    """The category the reviewer saved in the verified file; None when the file has none. A manifest guess
    or a clips.csv category never stands in for it."""
    data = _verified_file(video_id, verified_dir) or {}
    cat = data.get("category")
    return cat if isinstance(cat, str) and cat else None


def check_verified(clips: pd.DataFrame, verified_dir: Path) -> CheckResult:
    """Diversity rules on the verified clips of one language: >= 30 channels, largest channel <= 5%,
    >= 6 categories not counting `other`. `clips` has one row per verified clip with `video_id` and
    `channel` (or `channel_id`); categories come from the verified files only, so guesses never count."""
    df = clips.drop_duplicates("video_id").copy() if len(clips) else clips.copy()
    if "channel_id" not in df:
        df["channel_id"] = df["channel"] if "channel" in df else None
    df["category"] = [verified_category(str(v), verified_dir) for v in df["video_id"]] if len(df) else []
    df["category_guess"] = False
    return check_set(df, strict=True)


def diversity_contract(res: CheckResult) -> dict:
    """The verified_diversity object of the export contract (docs/V3.md, "Publication gate fields")."""
    return {
        "channels": res.n_channels,
        "largest_channel_share": round(res.top_share, 4) if res.n_clips else None,
        "categories": sorted(c for c in res.categories if c != "other"),
        "passes": res.ok,
        "problems": list(res.failures),
    }


def usable_verified(data: dict | None) -> bool:
    """Same rule as clipbench.data.references._verified: not skipped, not unusable, non-empty text."""
    if not data or data.get("skipped") or "unusable" in (data.get("tags") or []):
        return False
    text = data.get("text")
    return isinstance(text, str) and bool(text.strip())


def same_audio(data: dict, video_id: str, hashes: dict | None) -> bool:
    """False when the verified file names a WAV hash and hashes.json now holds a different one (the audio
    was fetched again after review). clipbench.data.references.load_clips drops those clips, so the
    funnel and `data check --verified` must not count them as verified either."""
    saved = data.get("audio_sha256")
    current = ((hashes or {}).get(video_id) or {}).get("wav")
    return not (saved and current and saved != current)


def verified_sample(sample: pd.DataFrame, verified_dir: Path, hashes: dict | None = None) -> pd.DataFrame:
    """Sample rows whose verified file is usable and, with `hashes`, made against the WAV on disk now
    (for `clipbench data check --verified`)."""
    keep = []
    for v in sample["video_id"]:
        data = _verified_file(str(v), verified_dir)
        keep.append(usable_verified(data) and same_audio(data, str(v), hashes))
    return sample[keep] if len(sample) else sample


def funnel(sample: pd.DataFrame, audio_dir: Path, verified_dir: Path, hashes: dict) -> dict[str, int]:
    """Clip counts from the sample to the verified set: sampled, audio available (WAV and hash on disk),
    reviewed (a verified file exists), skipped, unusable, verified (usable file made against the WAV on
    disk now; a file whose audio_sha256 no longer matches is reviewed but not verified). Per provider scored
    counts come from the run (clipbench.report.rules)."""
    out = {"sampled": 0, "audio_available": 0, "reviewed": 0, "skipped": 0, "unusable": 0, "verified": 0}
    for vid in sample["video_id"].astype(str) if len(sample) else []:
        out["sampled"] += 1
        if (audio_dir / f"{vid}.wav").is_file() and (hashes.get(vid) or {}).get("wav"):
            out["audio_available"] += 1
        data = _verified_file(vid, verified_dir)
        if data is None:
            continue
        out["reviewed"] += 1
        if data.get("skipped"):
            out["skipped"] += 1
        elif "unusable" in (data.get("tags") or []):
            out["unusable"] += 1
        elif usable_verified(data) and same_audio(data, vid, hashes):
            out["verified"] += 1
    return out
