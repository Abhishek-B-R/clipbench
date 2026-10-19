"""Publication rules: the one place that decides whether a language gets a ranking.

docs/V2.md, "Languages": a language is published only when its latest run has at least
MIN_VERIFIED_CLIPS verified clips AND at least MIN_PROVIDERS_OK providers with ok rows on them.
Below that every output (report.md, site/, the web export, OG images) shows the method and progress,
never a ranking, and no findings. The web app mirrors these numbers in web/src/lib/config.ts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from clipbench.config import Settings

LANGUAGES = ("en", "ja", "ko", "es", "hi")
MIN_VERIFIED_CLIPS = 100
MIN_PROVIDERS_OK = 6
# Same rule as clipbench.scoring.score.TOO_SMALL: a slice with fewer clips supports no claim.
MIN_SLICE_CLIPS = 15


@dataclass
class LanguageStatus:
    lang: str
    verified_clips: int = 0
    providers_ok: int = 0
    published: bool = False
    channels: int = 0
    categories: list[str] = field(default_factory=list)
    largest_channel_share: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_published(verified_clips: int, providers_ok: int) -> bool:
    return verified_clips >= MIN_VERIFIED_CLIPS and providers_ok >= MIN_PROVIDERS_OK


def _rows(settings: Settings, run_id: str) -> pd.DataFrame | None:
    path = settings.runs_dir / run_id / "rows.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - unreadable rows count as missing
        return None


def run_status(
    run_id: str, language: str, aggregates: pd.DataFrame, scores: pd.DataFrame | None, settings: Settings
) -> LanguageStatus:
    """Publication status of one run's language.

    verified_clips: distinct verified clips scored (scores.parquet, else the verified 'all' aggregate).
    providers_ok: providers with at least one ok repeat-0 row on a verified clip (rows.parquet); without
    rows.parquet, providers with a verified 'all' aggregate. channels, categories and the largest channel
    share describe the verified clips when there are any, else every clip in the run.
    """
    st = LanguageStatus(lang=language)
    verified_ids: set[str] = set()
    all_ids: set[str] = set()
    if scores is not None and not scores.empty:
        all_ids = set(scores["video_id"].astype(str))
        verified_ids = set(scores.loc[scores["tier"] == "verified", "video_id"].astype(str))
    if not verified_ids and not aggregates.empty:
        ver = aggregates[(aggregates["tier"] == "verified") & (aggregates["slice_kind"] == "all")]
        st.verified_clips = int(ver["n_clips"].max()) if len(ver) else 0
    else:
        st.verified_clips = len(verified_ids)

    rows = _rows(settings, run_id)
    if rows is not None and {"provider", "status", "video_id"} <= set(rows.columns):
        ok = rows[rows["status"] == "ok"]
        if "repeat" in ok:
            ok = ok[ok["repeat"] == 0]
        if "tier" in ok:
            ok = ok[ok["tier"] == "verified"]
        elif verified_ids:
            ok = ok[ok["video_id"].astype(str).isin(verified_ids)]
        st.providers_ok = int(ok["provider"].nunique())
    elif not aggregates.empty:
        ver = aggregates[(aggregates["tier"] == "verified") & (aggregates["slice_kind"] == "all")]
        st.providers_ok = int(ver.loc[ver["n_clips"] > 0, "provider"].nunique())

    ids = verified_ids or all_ids
    if rows is not None and {"video_id", "channel"} <= set(rows.columns):
        ch = rows.drop_duplicates("video_id")
        if ids:
            ch = ch[ch["video_id"].astype(str).isin(ids)]
        counts = Counter(str(c) for c in ch["channel"].dropna())
        st.channels = len(counts)
        total = sum(counts.values())
        st.largest_channel_share = round(max(counts.values()) / total, 4) if total else None
    if scores is not None and "category" in scores:
        sub = scores[scores["video_id"].astype(str).isin(ids)] if ids else scores
        st.categories = sorted({str(c) for c in sub["category"].dropna() if str(c)})
    st.published = is_published(st.verified_clips, st.providers_ok)
    return st


def all_languages(statuses: dict[str, LanguageStatus]) -> list[dict[str, Any]]:
    """One entry per v2 language, zeros for languages without a run, plus any other language seen."""
    langs = list(LANGUAGES) + sorted(set(statuses) - set(LANGUAGES))
    return [(statuses.get(lang) or LanguageStatus(lang=lang)).as_dict() for lang in langs]
