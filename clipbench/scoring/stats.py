"""Bootstrap confidence intervals and paired bootstrap comparisons.

Estimand: every rate is a corpus rate, sum of errors over sum of reference tokens across the clips, so long
clips weigh more than short ones. The main method resamples clips (each clip an exchangeable unit). The
cluster variants resample channels instead, as a sensitivity analysis for clips from one channel being
alike; they never replace the clip bootstrap.

Intervals are expanded percentile intervals (Hesterberg 2015): the percentile bootstrap is too narrow at
small n, so the tail level alpha/2 is replaced by Phi(-sqrt(n / (n - 1)) * t_{1 - alpha/2, n - 1}). At
n = 15 a plain percentile 95% interval excluded zero in about 8.5% of simulated null datasets; the expanded
one in about 5.5% (METHODOLOGY.md, "Statistical assumptions and limitations"; tests/test_stats_simulation.py).
The two sided p value is the alpha at which the expanded interval just reaches zero, from (k + 1) / (B + 1)
with k the resamples on the other side of zero, so p <= alpha exactly when the interval excludes zero and p
is never below what B resamples can resolve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

import numpy as np

N_RESAMPLES = 1000
DEFAULT_SEED = 7
ALPHA = 0.05

Verdict = Literal["a_better", "b_better", "no_difference"]

_NORMAL = NormalDist()


def corpus_rate(errors: np.ndarray, ref_lens: np.ndarray) -> float:
    total = float(np.sum(ref_lens))
    return float(np.sum(errors)) / total if total > 0 else 0.0


def mean_clip_rate(errors, ref_lens) -> float:
    """Secondary number: the plain mean of per clip error rates (clips with a non-empty reference)."""
    e = np.asarray(errors, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    keep = r > 0
    return float(np.mean(e[keep] / r[keep])) if keep.any() else float("nan")


def _resampled_rates(errors: np.ndarray, ref_lens: np.ndarray, idx: np.ndarray) -> np.ndarray:
    num = errors[idx].sum(axis=1)
    den = ref_lens[idx].sum(axis=1).astype(float)
    return np.divide(num, den, out=np.zeros_like(den), where=den > 0)


def _indices(n: int, n_resamples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(n_resamples, n))


# ---------------------------------------------------------------- expanded percentile levels


def _t_ppf(p: float, nu: int) -> float:
    """Student t quantile from the normal one (Cornish-Fisher, three terms). Within 0.1% of the exact value
    for nu >= 10 at the levels used here; about 3% low at nu = 2."""
    z = _NORMAL.inv_cdf(p)
    return (
        z
        + (z**3 + z) / (4 * nu)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * nu**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * nu**3)
    )


def tail_level(alpha: float, n: int) -> float:
    """Lower tail quantile level of the expanded percentile interval for n units (clips or channels)."""
    if n < 2:
        return alpha / 2
    t = _t_ppf(1 - alpha / 2, n - 1)
    return _NORMAL.cdf(-math.sqrt(n / (n - 1)) * t)


def _p_from_tail(share: float, n: int) -> float:
    """Smallest alpha whose expanded interval excludes zero, given the share of resamples on the other side
    of zero (zero counted as the other side). Bisection; tail_level is increasing in alpha."""
    if share >= 0.5:
        return 1.0
    if share <= 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if tail_level(mid, n) >= share:
            hi = mid
        else:
            lo = mid
    return float(min(1.0, hi))


def _interval(values: np.ndarray, alpha: float, n: int) -> tuple[float, float]:
    """Order statistics d_(K+1) and d_(B-K) of the B resampled values, K = floor(a (B + 1)) - 1 with a the
    expanded tail level: the (B + 1) convention, so the interval excludes zero exactly when the p value of
    _paired_from_diffs is <= alpha. With too few resamples for the level (K < 0) it is the full range."""
    v = np.sort(np.asarray(values, dtype=float))
    b = len(v)
    k = max(0, math.floor(tail_level(alpha, n) * (b + 1)) - 1)
    k = min(k, (b - 1) // 2)
    return float(v[k]), float(v[b - 1 - k])


# ---------------------------------------------------------------- single rate


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
    """Expanded percentile bootstrap CI of the corpus error rate, resampling clips."""
    e = np.asarray(errors, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    n = len(e)
    if n == 0:
        return RateCI(float("nan"), float("nan"), float("nan"), 0)
    rates = _resampled_rates(e, r, _indices(n, n_resamples, seed))
    low, high = _interval(rates, alpha, n)
    return RateCI(corpus_rate(e, r), low, high, n)


def _cluster_sums(clusters, *arrays) -> tuple[np.ndarray, ...]:
    """Per cluster sums of each array, clusters in sorted order."""
    keys = np.asarray([str(c) for c in clusters])
    _, inv = np.unique(keys, return_inverse=True)
    g = int(inv.max()) + 1 if len(inv) else 0
    return tuple(np.bincount(inv, weights=np.asarray(a, dtype=float), minlength=g) for a in arrays)


def cluster_bootstrap_ci(
    errors,
    ref_lens,
    clusters,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = ALPHA,
) -> RateCI:
    """Sensitivity analysis: the same corpus rate, resampling whole clusters (channels) instead of clips.

    Each resample draws G channels with replacement and pools all their clips. `n` is the number of
    channels. Fewer than 2 channels gives no interval (nan)."""
    e = np.asarray(errors, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    if len(e) == 0:
        return RateCI(float("nan"), float("nan"), float("nan"), 0)
    eg, rg = _cluster_sums(clusters, e, r)
    g = len(eg)
    if g < 2:
        return RateCI(corpus_rate(e, r), float("nan"), float("nan"), g)
    rates = _resampled_rates(eg, rg, _indices(g, n_resamples, seed))
    low, high = _interval(rates, alpha, g)
    return RateCI(corpus_rate(e, r), low, high, g)


# ---------------------------------------------------------------- paired


@dataclass
class PairedResult:
    n: int
    diff: float
    ci_low: float
    ci_high: float
    p_value: float
    verdict: Verdict


def _paired_from_diffs(diffs: np.ndarray, diff: float, n: int, alpha: float) -> PairedResult:
    low, high = _interval(diffs, alpha, n)
    b = len(diffs)
    # (k + 1) / (B + 1): a bootstrap p value never claims more precision than B resamples give (a plain k / B
    # of 0 made Benjamini-Hochberg reject far too often; tests/test_stats_simulation.py).
    if diff > 0:
        share = (int(np.sum(diffs <= 0)) + 1) / (b + 1)
    elif diff < 0:
        share = (int(np.sum(diffs >= 0)) + 1) / (b + 1)
    else:
        share = 0.5
    p_value = _p_from_tail(share, n)
    if high < 0:
        verdict: Verdict = "a_better"
    elif low > 0:
        verdict = "b_better"
    else:
        verdict = "no_difference"
    return PairedResult(n, float(diff), low, high, p_value, verdict)


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

    The same resampled clip indices are used for both providers in each resample. The CI is the expanded
    percentile interval of the resampled differences. p is two sided: the alpha at which that interval
    just reaches zero, from the share of resamples whose difference is on the other side of zero from the
    observed one (zero counts as the other side); p = 1 when the observed difference is 0. Verdict:
    a_better if the CI is entirely below 0, b_better if entirely above, which is p <= alpha.
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
    return _paired_from_diffs(diffs, corpus_rate(ea, r) - corpus_rate(eb, r), n, alpha)


def paired_cluster_bootstrap(
    errors_a,
    errors_b,
    ref_lens,
    clusters,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = ALPHA,
) -> PairedResult:
    """Sensitivity analysis: paired bootstrap resampling channels. `n` is the number of channels; fewer
    than 2 gives nan bounds, p = nan and no_difference."""
    ea = np.asarray(errors_a, dtype=float)
    eb = np.asarray(errors_b, dtype=float)
    r = np.asarray(ref_lens, dtype=float)
    if not (len(ea) == len(eb) == len(r) == len(clusters)):
        raise ValueError("errors_a, errors_b, ref_lens and clusters must have the same length")
    nan = float("nan")
    if len(r) == 0:
        return PairedResult(0, nan, nan, nan, nan, "no_difference")
    diff = corpus_rate(ea, r) - corpus_rate(eb, r)
    ag, bg, rg = _cluster_sums(clusters, ea, eb, r)
    g = len(rg)
    if g < 2:
        return PairedResult(g, float(diff), nan, nan, nan, "no_difference")
    idx = _indices(g, n_resamples, seed)
    diffs = _resampled_rates(ag, rg, idx) - _resampled_rates(bg, rg, idx)
    return _paired_from_diffs(diffs, diff, g, alpha)
