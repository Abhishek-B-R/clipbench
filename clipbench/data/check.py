"""Diversity rules (docs/V2.md), enforced by `clipbench data check --lang L`.

Per language set: at least 30 channels, no channel above 5% of the set, at least 6 categories present
(not counting `other`). The set is the language's rows of data/sample.parquet by default, or the
eligible manifest pool. Guessed categories count unless `strict`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

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
            "  categories: " + (", ".join(f"{k} {v}" for k, v in sorted(self.categories.items())) or "none")
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
    guess = df["category_guess"].fillna(False).astype(bool) if "category_guess" in df else pd.Series(False, index=df.index)
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
