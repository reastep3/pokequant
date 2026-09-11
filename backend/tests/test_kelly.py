import numpy as np
import pytest

from app.quant.kelly import (
    grade_distribution_from_pop,
    kelly_continuous,
    kelly_discrete,
    kelly_portfolio,
    slab_acquisition_kelly,
    slab_holding_kelly,
)


def test_binary_bet_matches_closed_form():
    # even-money bet with p=0.6: f* = 2p - 1 = 0.2
    res = kelly_discrete(np.array([1.0, -1.0]), np.array([0.6, 0.4]), multiplier=1.0, cap=1.0)
    assert res.f_star == pytest.approx(0.2, abs=1e-6)
    assert res.growth_rate == pytest.approx(0.6 * np.log(1.2) + 0.4 * np.log(0.8), abs=1e-9)
    assert res.win_prob == pytest.approx(0.6)


def test_no_edge_gives_zero():
    res = kelly_discrete(np.array([1.0, -1.0]), np.array([0.5, 0.5]))
    assert res.f_star == 0.0 and res.f_scaled == 0.0


def test_ruin_bound_respected():
    # a -90% outcome caps f below 1/0.9
    res = kelly_discrete(np.array([5.0, -0.9]), np.array([0.5, 0.5]), multiplier=1.0, cap=10.0)
    assert 0 < res.f_star < 1 / 0.9
    assert all(1 + res.f_star * o.ret > 0 for o in res.outcomes)


def test_half_kelly_and_cap():
    res = kelly_discrete(np.array([1.0, -1.0]), np.array([0.9, 0.1]), multiplier=0.5, cap=0.25)
    assert res.f_star == pytest.approx(0.8, abs=1e-6)
    assert res.f_scaled == 0.25


def test_grade_distribution_smoothing_and_tilt():
    pop = {"10": 300, "9": 600, "8": 100}
    p = grade_distribution_from_pop(pop, alpha=0)
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["10"] == pytest.approx(0.3)
    tilted = grade_distribution_from_pop(pop, alpha=0, condition_tilt=-0.5)
    assert tilted["10"] < p["10"] and tilted["8"] > p["8"]
    smoothed = grade_distribution_from_pop({"10": 1, "9": 0, "8": 0}, alpha=5)
    assert smoothed["9"] > 0  # Dirichlet prior keeps unseen grades alive


def test_slab_acquisition_uses_raw_price_for_missing_grades():
    res = slab_acquisition_kelly(
        raw_price=100, slab_prices={"10": 1000, "9": 250}, grade_probs={"10": 0.3, "9": 0.5, "8": 0.2},
        grading_fee=25, shipping=8, sell_fee_pct=0.13, multiplier=1.0, cap=1.0,
    )
    labels = {o.label: o for o in res.outcomes}
    assert labels["8"].payoff == pytest.approx(100 * 0.87)
    assert res.expected_return > 0 and res.f_star > 0


def test_continuous_kelly():
    f, fs, g = kelly_continuous(0.10, 0.04, multiplier=0.5, cap=10)
    assert f == pytest.approx(2.5) and fs == pytest.approx(1.25)
    assert kelly_continuous(0.1, 0.0) == (0.0, 0.0, 0.0)


def test_holding_kelly_mean_reversion_adds_expected_return():
    rng = np.random.default_rng(0)
    lp = np.cumsum(rng.normal(0, 0.01, 200)) + np.log(100)
    base = slab_holding_kelly(lp, fair_log_price=None)
    cheap = slab_holding_kelly(lp, fair_log_price=lp[-1] + 0.3, reversion_half_life_days=30)
    assert cheap["mu_h"] > base["mu_h"]


def test_portfolio_kelly_long_only_and_gross_cap():
    mu = np.array([0.10, 0.05, -0.02])
    cov = np.diag([0.04, 0.02, 0.03])
    f = kelly_portfolio(mu, cov, max_gross=1.0, multiplier=1.0)
    assert f.min() >= 0 and f.sum() <= 1.0 + 1e-6
    assert f[2] == 0.0 and f[0] > f[1]
