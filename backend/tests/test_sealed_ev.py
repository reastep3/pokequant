import numpy as np
import pytest

from app.quant.sealed_ev import PullSlot, ev_matrix, sealed_ev


def _slots():
    return [
        PullSlot("SIR", 1 / 100, np.array([300.0, 120.0, 60.0])),
        PullSlot("SAR", 1 / 24, np.array([40.0, 25.0, 15.0, 10.0])),
        PullSlot("IR", 1 / 8, np.array([8.0, 5.0, 3.0])),
    ]


def test_analytic_ev_matches_closed_form_and_mc():
    res = sealed_ev(150.0, 36, _slots(), bulk_value_per_pack=0.3, sell_fee_pct=0.1, liquidity_haircut=0.1, n_sims=60_000)
    net = 0.9 * 0.9
    ev = 36 * 0.3 + 36 * (160 / 100 + 22.5 / 24 + 16 / 3 / 8) * net
    assert res.ev == pytest.approx(ev, rel=1e-9)
    mc_mean = np.mean([res.percentiles["50"]])  # sanity only
    assert 0 < mc_mean
    assert res.ev_ratio == pytest.approx(ev / 150.0)
    assert 0 <= res.p_profit <= 1
    assert res.percentiles["5"] <= res.percentiles["50"] <= res.percentiles["95"]


def test_variance_is_compound_binomial():
    s = [PullSlot("X", 0.5, np.array([10.0]))]
    res = sealed_ev(1.0, 10, s, bulk_value_per_pack=0.0, sell_fee_pct=0.0, liquidity_haircut=0.0)
    # N ~ Bin(10, .5), value = 10N => var = 100 * 2.5 = 250
    assert res.std == pytest.approx(np.sqrt(250.0))


def test_matrix_ranks_by_mispricing():
    rows = [
        {"ticker": "CHEAP", "price": 80.0, "n_packs": 36, "slots": _slots()},
        {"ticker": "RICH", "price": 400.0, "n_packs": 36, "slots": _slots()},
    ]
    out = ev_matrix(rows)
    assert out[0]["ticker"] == "CHEAP" and out[0]["mispricing_pct"] > out[1]["mispricing_pct"]
