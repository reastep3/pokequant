import numpy as np
import pytest

from app.quant.arbitrage import arb_signal, grading_arb, pairs_spread, rolling_zscore


def test_grading_arb_expected_value_and_breakeven():
    probs = {"10": 0.4, "9": 0.5, "8": 0.1}
    slabs = {"10": 500.0, "9": 150.0, "8": 80.0}
    res = grading_arb(150.0, slabs, probs, grading_fee=25, shipping=8, sell_fee_pct=0.1)
    egv = 0.4 * 450 + 0.5 * 135 + 0.1 * 72
    assert res.expected_graded_value == pytest.approx(egv)
    assert res.all_in_cost == pytest.approx(183)
    assert res.spread == pytest.approx(egv - 183)
    # recompute EGV at the breakeven gem rate, holding the 9/8 mix fixed -> equals cost
    p = res.breakeven_gem_rate
    v_rest = (0.5 * 135 + 0.1 * 72) / 0.6
    assert 0 < p < 1
    assert p * 450 + (1 - p) * v_rest == pytest.approx(183, abs=1e-6)


def test_rolling_zscore_no_lookahead():
    x = np.concatenate([np.zeros(30), [10.0]])
    z, mean, sd = rolling_zscore(x, window=30, min_periods=10)
    assert np.isinf(z[-1]) or np.isnan(z[-1])  # zero-variance history => undefined z
    x2 = np.concatenate([np.random.default_rng(1).normal(0, 1, 60), [5.0]])
    z2, _, _ = rolling_zscore(x2, window=30, min_periods=10)
    assert z2[-1] > 3


def test_arb_signal_direction():
    hist = np.concatenate([np.random.default_rng(2).normal(0, 1, 90), [6.0]])
    sig = arb_signal(hist, z_threshold=2.0)
    assert sig["direction"] == "long" and sig["z"] > 2


def test_pairs_half_life_recovers_ou_parameter():
    rng = np.random.default_rng(3)
    n, lam_true = 2000, -0.05  # half-life ~13.9 days
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = s[t - 1] + lam_true * s[t - 1] + rng.normal(0, 0.05)
    b = np.zeros(n) + 4.0  # stationary AR(1) regressor (log EN price around a level)
    for t in range(1, n):
        b[t] = 4.0 + 0.9 * (b[t - 1] - 4.0) + rng.normal(0, 0.3)
    a = 0.5 + 1.2 * b + s
    res = pairs_spread(a, b)
    assert res.beta == pytest.approx(1.2, abs=0.05)
    assert res.half_life_days == pytest.approx(-np.log(2) / lam_true, rel=0.25)
    assert abs(res.z) < 4
