import numpy as np
import pytest

from app.quant.kernel_regression import (
    fair_value_trendline,
    local_linear,
    nadaraya_watson,
    silverman_bandwidth,
)


def test_local_linear_is_exact_on_linear_data():
    t = np.linspace(0, 100, 40)
    y = 2.0 + 0.03 * t
    fit, _ = local_linear(t, y, np.array([0.0, 50.0, 100.0]), h=15.0)
    assert np.allclose(fit, [2.0, 3.5, 5.0], atol=1e-9)
    nw = nadaraya_watson(t, y, np.array([100.0]), h=15.0)
    assert nw[0] < 5.0  # NW is biased at the boundary; LL is not


def test_trendline_recovers_log_linear_trend_with_outliers():
    rng = np.random.default_rng(0)
    t = np.sort(rng.uniform(0, 365, 80))
    true_lp = np.log(200) + 0.002 * t
    p = np.exp(true_lp + rng.normal(0, 0.05, t.size))
    p[10] *= 4.0  # mis-listed lot
    p[50] *= 0.2  # damaged slab
    res = fair_value_trendline(t, p, now_day=365.0, market_price=float(np.exp(true_lp[-1])))
    truth = np.exp(np.log(200) + 0.002 * 365)
    assert res.fair_value_now == pytest.approx(truth, rel=0.06)
    assert res.fair_lo_now < res.fair_value_now < res.fair_hi_now
    assert res.bandwidth_days >= 7.0
    assert abs(res.z_market_vs_fair) < 2.5
    assert res.method == "local_linear_robust"


def test_sparse_fallback():
    res = fair_value_trendline(np.array([0.0, 10.0, 20.0]), np.array([100.0, 110.0, 105.0]))
    assert res.method == "median_fallback" and res.fair_value_now == pytest.approx(105.0)


def test_silverman_positive():
    assert silverman_bandwidth(np.array([1.0, 2.0, 3.0, 10.0])) > 0
