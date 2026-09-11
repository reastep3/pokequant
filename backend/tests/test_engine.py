import math

import numpy as np

from app.quant import QuantEngine, SealedInputs, SlabInputs
from app.quant.rl.env import EpisodeData, SlabSpreadEnv, episodes_from_snapshots
from app.quant.sealed_ev import PullSlot


def _series(n=400, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    p = 300 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    return t, p


def test_engine_slab_end_to_end():
    t, p = _series()
    _, en = _series(seed=1)
    months = 30
    x = SlabInputs(
        ticker="TEST-EN", grade_key="PSA10", ts_days=t, prices=p,
        market_price=float(p[-1]), raw_price=float(p[-1] * 0.2),
        slab_prices={"10": float(p[-1]), "9": float(p[-1] * 0.35), "8": float(p[-1] * 0.22)},
        pop_counts={"10": 800, "9": 2400, "8": 900, "7": 200},
        pop_history=[(float(d), 4000 + int(d * 1.5)) for d in range(0, 400, 7)],
        arb_spread_history=np.random.default_rng(5).normal(20, 8, 120),
        pair_log_prices=(np.log(p[-200:]), np.log(en[-200:])),
        cs_log_prices=np.log(p[::13][:months]), turnover_series=np.full(months, 30.0),
        pop_series=np.linspace(3000, 4300, months), age_years=np.linspace(20, 22, months),
        trades_30d=25, days_since_last_trade=1, price_cv_30d=0.08, n_listings=30,
    )
    out = QuantEngine().analyze_slab(x)
    for k in ("fair_value", "ev_gap_pct", "dd_30d", "dd_60d", "dd_90d", "kelly_fraction_scaled",
              "liquidity_score", "screener_score", "arb_spread", "pop_velocity_30d", "pairs_z",
              "cs_bubble_premium_pct", "kelly_adj_roi"):
        assert k in out and out[k] is not None and math.isfinite(out[k]), k
    assert 0 <= out["kelly_fraction_scaled"] <= 0.25
    assert out["pop_total"] == 4300
    assert "kelly_acquisition" in out["payload"] and "grading_arb" in out["payload"]


def test_engine_sealed_end_to_end():
    t, p = _series(200, seed=3)
    x = SealedInputs(ticker="BOX", price=float(p[-1]), n_packs=36,
                     slots=[PullSlot("SIR", 0.01, np.array([250.0, 90.0]))], ts_days=t, prices=p, trades_30d=10, days_since_last_trade=2)
    out = QuantEngine().analyze_sealed(x)
    assert out["grade_key"] == "SEALED" and out["sealed_ev"] > 0 and "dd_90d" in out


def test_rl_env_mechanics():
    T = 30
    ep = EpisodeData(features=np.zeros((T, 6), dtype=np.float32), prices=np.linspace(100, 200, T),
                     raw_prices=np.full(T, 40.0), grading_cost=10.0, sell_fee_pct=0.0)
    env = SlabSpreadEnv(ep, capital=1000.0)
    s = env.reset()
    assert s.shape == (9,)
    s, r, done, _ = env.step(1)  # buy raw at 40 + 10, marked at slab price -> instant MTM gain
    assert env.position == 1 and r > 0
    total = r
    while not done:
        s, r, done, info = env.step(0)
        total += r
    assert info["value"] > 1000.0
    rows = [{"arb_spread_z": 0.1, "market_price": 100.0, "raw_price": 30.0} for _ in range(12)]
    assert episodes_from_snapshots(rows) is not None
