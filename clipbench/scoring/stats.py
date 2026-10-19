"""Bootstrap confidence intervals and paired bootstrap comparisons over clips.

The unit of resampling is the clip. Every rate is a corpus rate: sum of errors over sum of
reference tokens across the resampled clips, so long clips weigh more than short ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

N_RESAMPLES = 1000
DEFAULT_SEED = 7
ALPHA = 0.05

Verdict = Literal["a_better", "b_better", "no_difference"]


def corpus_rate(errors: np.ndarray, ref_lens: np.ndarray) -> float:
    total = float(np.sum(ref_lens))
    return float(np.sum(errors)) / total if total > 0 else 0.0


def _resampled_rates(errors: np.ndarray, ref_lens: np.ndarray, idx: np.ndarray) -> np.ndarray:
    num = errors[idx].sum(axis=1)
    den = ref_lens[idx].sum(axis=1).astype(float)
    return np.divide(num, den, out=np.zeros_like(den), where=den > 0)


def _indices(n: int, n_resamples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(n_resamples, n))


@dataclass
class RateCI:
    rate: float
    ci_low: float
    ci_high: float
    n: int


def bootstrap_ci(
    errors,
    ref_lens,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = ALPHA,
) -> RateCI:
    """Percentile bootstrap CI of the corpus error rate over clips."""
    e = np.asarray(errors, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    n = len(e)
    if n == 0:
        return RateCI(float("nan"), float("nan"), float("nan"), 0)
    rates = _resampled_rates(e, r, _indices(n, n_resamples, seed))
    low, high = np.quantile(rates, [alpha / 2, 1 - alpha / 2])
    return RateCI(corpus_rate(e, r), float(low), float(high), n)


@dataclass
class PairedResult:
    n: int
    diff: float
    ci_low: float
    ci_high: float
    p_value: float
    verdict: Verdict


def paired_bootstrap(
    errors_a,
    errors_b,
    ref_lens,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = ALPHA,
) -> PairedResult:
    """Paired bootstrap of rate_a - rate_b over clips both providers scored.

    The same resampled clip indices are used for both providers in each resample. p is two sided:
    twice the share of resamples whose difference has the opposite sign to the observed one (or is
    zero), capped at 1. Verdict: a_better if the CI is entirely below 0, b_better if entirely above.
    """
    ea = np.asarray(errors_a, dtype=float)
    eb = np.asarray(errors_b, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    n = len(r)
    if not (len(ea) == len(eb) == n):
        raise ValueError("errors_a, errors_b and ref_lens must have the same length")
    if n == 0:
        nan = float("nan")
        return PairedResult(0, nan, nan, nan, 1.0, "no_difference")

    idx = _indices(n, n_resamples, seed)
    diffs = _resampled_rates(ea, r, idx) - _resampled_rates(eb, r, idx)
    diff = corpus_rate(ea, r) - corpus_rate(eb, r)
    low, high = (float(x) for x in np.quantile(diffs, [alpha / 2, 1 - alpha / 2]))

    if diff > 0:
        flips = np.mean(diffs <= 0)
    elif diff < 0:
        flips = np.mean(diffs >= 0)
    else:
        flips = 0.5
    p_value = float(min(1.0, 2 * flips))

    if high < 0:
        verdict: Verdict = "a_better"
    elif low > 0:
        verdict = "b_better"
    else:
        verdict = "no_difference"
    return PairedResult(n, float(diff), low, high, p_value, verdict)
