import numpy as np
import pytest

from app.quant.drawdown import drawdown_profile, realized_vol_annualized, window_stats
from app.quant.liquidity import liquidity_score
from app.quant.screener import screener_score


def test_drawdown_and_recovery():
    t = np.arange(0, 100, dtype=float)
    p = np.full(100, 100.0)
    p[20:40] = np.linspace(100, 70, 20)  # decline to trough at day 39
    p[40:60] = np.linspace(70, 101, 20)  # recover by day 59
    p[60:] = 101.0
    st = window_stats(t, p, 90)
    assert st.max_drawdown == pytest.approx(-0.3, abs=0.02)
    assert st.recovery_days == 20
    assert st.underwater_days == 0


def test_unrecovered_drawdown():
    t = np.arange(0, 60, dtype=float)
    p = np.concatenate([np.full(30, 100.0), np.linspace(100, 80, 30)])
    st = window_stats(t, p, 60)
    assert st.recovery_days is None and st.current_drawdown == pytest.approx(-0.2)
    assert st.underwater_days > 0
    prof = drawdown_profile(t, p)
    assert set(prof) == {30, 60, 90}


def test_vol_and_liquidity_monotone():
    t = np.arange(0, 120, dtype=float)
    p = 100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.02, 120)))
    assert realized_vol_annualized(t, p) == pytest.approx(0.02 * np.sqrt(365), rel=0.3)
    lo = liquidity_score(trades_30d=1, days_since_last_trade=20, price_cv_30d=0.4, n_listings=2)["score"]
    hi = liquidity_score(trades_30d=40, days_since_last_trade=0, price_cv_30d=0.05, n_listings=50, spread_pct=0.02)["score"]
    assert 0 <= lo < hi <= 100


def test_screener_score_penalizes_bubble_rewards_liquidity():
    base = screener_score(kelly_adj_roi=0.05, liquidity=50)
    assert screener_score(kelly_adj_roi=0.05, liquidity=100) > base
    assert screener_score(kelly_adj_roi=0.05, liquidity=50, bubble_premium_pct=0.8) < base
    assert screener_score(kelly_adj_roi=None, liquidity=50) == 0
