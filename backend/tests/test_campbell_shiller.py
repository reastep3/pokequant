import numpy as np
import pytest

from app.quant.campbell_shiller import (
    campbell_shiller,
    log_linearize,
    scarcity_nostalgia_fundamental,
)


def _fundamental(n, rng):
    d = np.cumsum(rng.normal(0.004, 0.02, n)) + 1.0
    return d


def test_log_linearization_constants():
    p = np.log(np.full(50, 100.0))
    d = np.log(np.full(50, 4.0))
    rho, kappa = log_linearize(p, d)
    assert rho == pytest.approx(1 / (1 + 0.04))
    assert np.isfinite(kappa)


def test_pure_fundamental_has_small_bubble_share():
    rng = np.random.default_rng(1)
    n = 240
    d = _fundamental(n, rng)
    p = d + 3.0 + rng.normal(0, 0.02, n)
    res = campbell_shiller(p, d)
    assert res is not None
    assert abs(res.var_share_cf + res.var_share_dr + res.var_share_bubble - 1) < 1e-6
    assert abs(res.bubble_premium_pct) < 0.15
    assert res.var_share_bubble < 0.2  # VAR residual carries estimation noise only
    assert res.method == "var"  # 240 obs
    lr = campbell_shiller(p, d, method="longrun")
    assert lr.var_share_bubble < 0.05  # white-noise ratio => phi ~ 0 => no persistence bubble share
    short = campbell_shiller(p[:30], d[:30])
    assert short is not None and short.method == "longrun" and abs(short.bubble_premium_pct) < 0.2


def test_injected_bubble_is_detected():
    rng = np.random.default_rng(2)
    n = 240
    d = _fundamental(n, rng)
    bubble = np.zeros(n)
    for t in range(1, n):
        bubble[t] = 0.97 * bubble[t - 1] + rng.normal(0, 0.05)
    bubble[-40:] += np.linspace(0, 0.8, 40)  # late-sample speculative run-up
    p = d + 3.0 + bubble + rng.normal(0, 0.02, n)
    for method in ("longrun", "var"):
        res = campbell_shiller(p, d, method=method)
        assert res is not None
        assert res.bubble_premium_pct > 0.2, method
        assert np.corrcoef(res.speculative, bubble[1:])[0, 1] > 0.5, method
        assert res.fundamental_value < np.exp(p[-1])
        assert abs(res.var_share_cf + res.var_share_dr + res.var_share_bubble - 1) < 1e-6
    lr = campbell_shiller(p, d, method="longrun")
    assert lr.var_share_bubble > 0.05  # persistent ratio => positive rational-bubble share
    assert lr.coefs["phi"] > 0.8


def test_explosive_ratio_is_capped_and_flagged():
    rng = np.random.default_rng(5)
    n = 60
    d = np.cumsum(rng.normal(0.0, 0.01, n))
    p = d + np.cumsum(np.full(n, 0.05)) + rng.normal(0, 0.01, n)  # ratio trends: explosive
    res = campbell_shiller(p, d, method="longrun")
    assert res is not None and res.stable is False
    assert np.isfinite(res.bubble_premium_pct) and res.var_share_bubble > 0.5


def test_short_series_returns_none():
    assert campbell_shiller(np.zeros(5), np.zeros(5)) is None


def test_fundamental_proxy_shape():
    d = scarcity_nostalgia_fundamental(np.array([10, 20]), np.array([100, 100]), np.array([1, 2]), 0.2)
    assert d.shape == (2,) and d[1] > d[0]
