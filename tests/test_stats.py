from __future__ import annotations

import numpy as np
import pytest

from clipbench.scoring.stats import bootstrap_ci, corpus_rate, paired_bootstrap


def test_corpus_rate_weights_by_length():
    assert corpus_rate(np.array([1, 9]), np.array([10, 10])) == pytest.approx(0.5)
    assert corpus_rate(np.array([1, 0]), np.array([1, 99])) == pytest.approx(0.01)


def test_bootstrap_deterministic_with_seed():
    rng = np.random.default_rng(1)
    errors = rng.integers(0, 10, 40)
    lens = rng.integers(10, 50, 40)
    a = bootstrap_ci(errors, lens, seed=3)
    b = bootstrap_ci(errors, lens, seed=3)
    c = bootstrap_ci(errors, lens, seed=4)
    assert (a.rate, a.ci_low, a.ci_high) == (b.rate, b.ci_low, b.ci_high)
    assert (a.ci_low, a.ci_high) != (c.ci_low, c.ci_high)
    assert a.ci_low <= a.rate <= a.ci_high
    assert a.n == 40


def test_bootstrap_empty():
    r = bootstrap_ci([], [])
    assert r.n == 0 and np.isnan(r.rate)


def test_paired_a_clearly_better():
    rng = np.random.default_rng(0)
    lens = rng.integers(20, 60, 50)
    err_a = (lens * 0.05).round()
    err_b = (lens * 0.30).round()
    res = paired_bootstrap(err_a, err_b, lens, seed=1)
    assert res.diff < 0
    assert res.ci_high < 0
    assert res.verdict == "a_better"
    assert res.p_value < 0.01
    flipped = paired_bootstrap(err_b, err_a, lens, seed=1)
    assert flipped.verdict == "b_better"


def test_paired_equal():
    lens = np.full(30, 40)
    errs = np.arange(30) % 7
    res = paired_bootstrap(errs, errs, lens, seed=1)
    assert res.diff == 0
    assert res.verdict == "no_difference"
    assert res.p_value == 1.0


def test_paired_noisy_no_difference():
    rng = np.random.default_rng(5)
    lens = np.full(30, 40)
    err_a = rng.integers(5, 15, 30)
    err_b = np.roll(err_a, 1)  # same distribution, same total
    res = paired_bootstrap(err_a, err_b, lens, seed=2)
    assert res.verdict == "no_difference"
    assert res.p_value > 0.05


def test_paired_deterministic():
    rng = np.random.default_rng(9)
    lens = rng.integers(10, 40, 25)
    a, b = rng.integers(0, 10, 25), rng.integers(0, 10, 25)
    assert paired_bootstrap(a, b, lens, seed=11) == paired_bootstrap(a, b, lens, seed=11)


def test_paired_length_mismatch():
    with pytest.raises(ValueError):
        paired_bootstrap([1, 2], [1], [3, 3])
