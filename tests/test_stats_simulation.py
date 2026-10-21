"""Deterministic simulations of the paired bootstrap, the Benjamini-Hochberg step and the channel-clustered
sensitivity analysis (METHODOLOGY.md, "Statistical assumptions and limitations").

Clips are drawn as exchangeable units: a reference length, a clip difficulty p ~ Beta(2, 14) (mean 12.5%),
and errors ~ Binomial(ref_len, p) for each provider. "Identical providers" share p on every clip. The fast
suite uses a few hundred datasets and 200 resamples; set CLIPBENCH_SLOW_TESTS=1 for the larger variant with
production settings (1000 resamples). Observed rates are written next to each assertion; the tolerance is
about two Monte Carlo standard errors above nominal.
"""

from __future__ import annotations

import math
import os
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from clipbench.report.findings import adjust_paired
from clipbench.scoring.stats import (
    bootstrap_ci,
    corpus_rate,
    paired_bootstrap,
    paired_cluster_bootstrap,
)

SLOW = os.environ.get("CLIPBENCH_SLOW_TESTS") == "1"


def clips(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    return rng.integers(15, 120, n), rng.beta(2, 14, n)


def errors(rng: np.random.Generator, ref: np.ndarray, p: np.ndarray, delta: float = 0.0) -> np.ndarray:
    return rng.binomial(ref, np.clip(p + delta, 0.0, 1.0))


def null_beats_rate(n: int, datasets: int, resamples: int, seed: int) -> tuple[float, float]:
    """(share of datasets with a "beats" verdict, share where verdict and p <= 0.05 agree)."""
    rng = np.random.default_rng(seed)
    beats = agree = 0
    for s in range(datasets):
        ref, p = clips(rng, n)
        res = paired_bootstrap(errors(rng, ref, p), errors(rng, ref, p), ref, n_resamples=resamples, seed=s)
        sig = res.verdict != "no_difference"
        beats += sig
        agree += sig == (res.p_value <= 0.05)
    return beats / datasets, agree / datasets


def tolerance(datasets: int, nominal: float = 0.05) -> float:
    return nominal + 2 * math.sqrt(nominal * (1 - nominal) / datasets)


@pytest.mark.parametrize(
    ("n", "observed"),
    [(15, 0.0467), (30, 0.0400), (100, 0.0433)],  # observed false "beats" rate with these seeds
)
def test_null_false_beats_rate_at_or_below_nominal(n, observed):
    datasets = 300
    rate, agree = null_beats_rate(n, datasets, 200, seed=100 + n)
    assert rate == pytest.approx(observed, abs=5e-4)
    assert rate <= tolerance(datasets)  # 0.075 at 300 datasets
    assert agree == 1.0  # the verdict (CI excludes 0) and p <= 0.05 never disagree


def test_expanded_interval_fixes_small_n_undercoverage():
    """At n = 15 the plain percentile interval calls 6.75% of these null datasets a win (8.5% over 2000
    datasets with 1000 resamples); the expanded interval 3.75%. Same resamples, only the tail differs."""
    from clipbench.scoring.stats import _indices, _interval, _resampled_rates

    rng = np.random.default_rng(5)
    n, datasets = 15, 400
    plain = expanded = 0
    for s in range(datasets):
        ref, p = clips(rng, n)
        ea, eb = errors(rng, ref, p), errors(rng, ref, p)
        idx = _indices(n, 400, s)
        d = _resampled_rates(ea.astype(float), ref.astype(float), idx) - _resampled_rates(
            eb.astype(float), ref.astype(float), idx
        )
        lo, hi = np.quantile(d, [0.025, 0.975])
        plain += hi < 0 or lo > 0
        lo, hi = _interval(d, 0.05, n)  # what bootstrap_ci and paired_bootstrap use
        expanded += hi < 0 or lo > 0
    assert plain / datasets == pytest.approx(0.0675, abs=1e-9)
    assert expanded / datasets == pytest.approx(0.0375, abs=1e-9)
    assert expanded < plain


def test_power_with_a_known_difference():
    """B makes 2 or 3 points more errors on every clip (12.5% vs 14.5% or 15.5%) at n = 100."""
    rng = np.random.default_rng(3)
    for delta, observed in ((0.02, 0.915), (0.03, 1.0)):
        hits = 0
        for s in range(200):
            ref, p = clips(rng, 100)
            res = paired_bootstrap(
                errors(rng, ref, p), errors(rng, ref, p, delta), ref, n_resamples=200, seed=s
            )
            hits += res.verdict == "a_better"
            assert res.verdict != "b_better"  # never the wrong direction
        assert hits / 200 == pytest.approx(observed, abs=1e-9)
        assert hits / 200 >= 0.8


def _bh_dataset(
    rng: np.random.Generator, offsets: dict[str, float], n: int, seed: int, resamples: int = 1000
) -> pd.DataFrame:
    ref, p = clips(rng, n)
    errs = {pid: errors(rng, ref, p, d) for pid, d in offsets.items()}
    rows = []
    for a, b in combinations(sorted(offsets), 2):
        res = paired_bootstrap(errs[a], errs[b], ref, n_resamples=resamples, seed=seed)
        rows.append({"tier": "verified", "metric": "cer", "slice_kind": "all", "slice": "all", "provider_a": a,
                     "provider_b": b, "n_clips": n, "diff": res.diff, "ci_low": res.ci_low, "ci_high": res.ci_high,
                     "p_value": res.p_value, "verdict": res.verdict})  # fmt: skip
    return pd.DataFrame(rows)


def _false(row, offsets) -> bool:
    """A discovery is false when the providers are identical or the direction is wrong."""
    da, db = offsets[row.provider_a], offsets[row.provider_b]
    if da == db:
        return True
    return (row.verdict == "a_better") != (da < db)


def test_bh_controls_false_discovery_proportion_in_mixed_scenario():
    """Six providers: four identical (6 null pairs), two clearly different (9 real pairs); 15 tests per run."""
    offsets = {"n1": 0.0, "n2": 0.0, "n3": 0.0, "n4": 0.0, "good": -0.04, "bad": 0.05}
    rng = np.random.default_rng(21)
    fdp, power, runs = [], [], 150
    for s in range(runs):
        adj = adjust_paired(_bh_dataset(rng, offsets, 100, s))
        found = adj[adj["verdict"] != "no_difference"]
        false = sum(_false(r, offsets) for r in found.itertuples(index=False))
        fdp.append(false / max(len(found), 1))
        real = [r for r in adj.itertuples(index=False) if offsets[r.provider_a] != offsets[r.provider_b]]
        power.append(sum(r.verdict != "no_difference" and not _false(r, offsets) for r in real) / len(real))
    mean_fdp = float(np.mean(fdp))
    assert mean_fdp == pytest.approx(0.0226, abs=5e-4)  # observed false discovery rate
    assert mean_fdp <= 0.05
    assert float(np.mean(power)) >= 0.9


def test_bh_under_the_global_null():
    """Six identical providers, 15 tests per run: BH keeps the chance of any "beats" at about q."""
    offsets = dict.fromkeys(("a", "b", "c", "d", "e", "f"), 0.0)
    rng = np.random.default_rng(22)
    runs = 150
    any_win = sum(
        (adjust_paired(_bh_dataset(rng, offsets, 100, s))["verdict"] != "no_difference").any()
        for s in range(runs)
    )
    # Observed 0.040. The previous p value (2 k / B, so 0 when no resample crossed zero) gave 0.073 here:
    # BH accepted p = 0 at any number of tests.
    assert any_win / runs == pytest.approx(0.04, abs=1e-9)
    assert any_win / runs <= tolerance(runs)


def test_channel_clustering_breaks_the_clip_bootstrap_and_the_cluster_one_holds():
    """Null on average, but each of 30 channels favours one provider by a random amount (sd 4 points). The
    clip bootstrap treats the 100 clips as independent and finds many false wins; resampling channels does
    not. This is why the clustered interval is reported as a sensitivity check."""
    rng = np.random.default_rng(23)
    runs, clip_fp, cluster_fp = 300, 0, 0
    for s in range(runs):
        ref, p = clips(rng, 100)
        ch = rng.integers(0, 30, 100)
        eff = rng.normal(0, 0.04, 30)[ch]
        ea, eb = errors(rng, ref, p + eff / 2), errors(rng, ref, p - eff / 2)
        clip_fp += paired_bootstrap(ea, eb, ref, n_resamples=200, seed=s).verdict != "no_difference"
        cluster_fp += (
            paired_cluster_bootstrap(ea, eb, ref, ch, n_resamples=200, seed=s).verdict != "no_difference"
        )
    assert clip_fp / runs == pytest.approx(0.1167, abs=5e-4)
    assert cluster_fp / runs == pytest.approx(0.0367, abs=5e-4)
    assert cluster_fp / runs <= tolerance(runs) < clip_fp / runs


def test_single_rate_ci_coverage():
    """The corpus error rate interval covers the true corpus rate about 95% of the time at n = 100."""
    rng = np.random.default_rng(24)
    runs, covered = 300, 0
    ref_pop, p_pop = clips(rng, 20000)
    truth = corpus_rate(ref_pop * p_pop, ref_pop)
    for s in range(runs):
        pick = rng.integers(0, len(ref_pop), 100)
        ref, p = ref_pop[pick], p_pop[pick]
        ci = bootstrap_ci(errors(rng, ref, p), ref, n_resamples=200, seed=s)
        covered += ci.ci_low <= truth <= ci.ci_high
    assert covered / runs == pytest.approx(0.9633, abs=5e-4)
    assert covered / runs >= 0.95 - 2 * math.sqrt(0.05 * 0.95 / runs)


@pytest.mark.skipif(not SLOW, reason="slow variant: set CLIPBENCH_SLOW_TESTS=1")
@pytest.mark.parametrize("n", [15, 30, 60, 100, 200])
def test_null_rate_slow(n):
    """Production settings: 1000 resamples, 2000 datasets. Tolerance 0.05 + 2 SE = 0.0597."""
    rate, agree = null_beats_rate(n, 2000, 1000, seed=200 + n)
    assert rate <= tolerance(2000) and agree == 1.0
