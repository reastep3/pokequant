"""QuantEngine: façade that composes the Alpha Module into per-asset analytics.

Pure computation — callers (services/analytics_service.py) load rows from PostgreSQL and hand
numpy arrays in; the engine returns plain dicts that map 1:1 onto ``AnalyticsSnapshot``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from app.quant.arbitrage import arb_signal, grading_arb, pairs_spread
from app.quant.campbell_shiller import campbell_shiller, scarcity_nostalgia_fundamental
from app.quant.drawdown import drawdown_profile, realized_vol_annualized
from app.quant.kelly import grade_distribution_from_pop, slab_acquisition_kelly, slab_holding_kelly
from app.quant.kernel_regression import fair_value_trendline
from app.quant.liquidity import liquidity_score
from app.quant.screener import kelly_adjusted_roi, screener_score
from app.quant.sealed_ev import PullSlot, sealed_ev


@dataclass
class EngineParams:
    grading_fee: float = 25.0
    shipping: float = 8.0
    sell_fee_pct: float = 0.1325
    rf_annual: float = 0.045
    kelly_multiplier: float = 0.5
    kelly_cap: float = 0.25
    z_threshold: float = 2.0
    horizon_days: int = 90
    condition_tilt: float = -0.15  # default pessimism vs pop-report gem rates for unknown raw
    bankroll: float = 1.0


@dataclass
class SlabInputs:
    ticker: str
    grade_key: str
    # Daily (or irregular) bar series for the slab grade being analyzed
    ts_days: np.ndarray
    prices: np.ndarray
    # Individual sales for the kernel regression (may be the same as bars)
    sales_ts_days: np.ndarray | None = None
    sales_prices: np.ndarray | None = None
    market_price: float | None = None
    raw_price: float | None = None
    slab_prices: Mapping[str, float] = field(default_factory=dict)  # latest price by grade
    pop_counts: Mapping[str, int] = field(default_factory=dict)
    pop_history: list[tuple[float, int]] = field(default_factory=list)  # (day, total)
    arb_spread_history: np.ndarray | None = None  # precomputed daily spread series
    pair_log_prices: tuple[np.ndarray, np.ndarray] | None = None  # (this, counterpart) aligned
    turnover_series: np.ndarray | None = None  # monthly turnover aligned with cs_log_prices
    pop_series: np.ndarray | None = None
    cs_log_prices: np.ndarray | None = None
    age_years: np.ndarray | None = None
    trades_30d: float = 0.0
    days_since_last_trade: float = 999.0
    price_cv_30d: float | None = None
    n_listings: float | None = None
    spread_pct: float | None = None


@dataclass
class SealedInputs:
    ticker: str
    price: float
    n_packs: int
    slots: list[PullSlot]
    bulk_value_per_pack: float = 0.35
    liquidity_haircut: float = 0.15
    ts_days: np.ndarray | None = None
    prices: np.ndarray | None = None
    trades_30d: float = 0.0
    days_since_last_trade: float = 999.0
    price_cv_30d: float | None = None
    n_listings: float | None = None


class QuantEngine:
    def __init__(self, params: EngineParams | None = None):
        self.p = params or EngineParams()

    # ------------------------------------------------------------------ slabs
    def analyze_slab(self, x: SlabInputs) -> dict:
        p = self.p
        out: dict = {"ticker": x.ticker, "grade_key": x.grade_key, "payload": {}}
        ts, px = np.asarray(x.ts_days, float), np.asarray(x.prices, float)
        if ts.size == 0:
            return out
        market = float(x.market_price if x.market_price is not None else px[-1])
        out["market_price"] = market

        # --- fair value (kernel regression) ------------------------------------------------
        s_ts = x.sales_ts_days if x.sales_ts_days is not None else ts
        s_px = x.sales_prices if x.sales_prices is not None else px
        fv = fair_value_trendline(np.asarray(s_ts, float), np.asarray(s_px, float),
                                  now_day=float(ts[-1]), market_price=market)
        out.update(fair_value=fv.fair_value_now, fair_value_lo=fv.fair_lo_now, fair_value_hi=fv.fair_hi_now,
                   ev_gap_pct=(fv.fair_value_now - market) / market if market else None)
        out["payload"]["fair_value"] = fv.as_dict()

        # --- drawdowns / vol --------------------------------------------------------------
        dd = drawdown_profile(ts, px)
        for w, st in dd.items():
            out[f"dd_{w}d"] = st.max_drawdown
            out[f"recovery_{w}d_days"] = st.recovery_days
        out["underwater_days"] = dd[90].underwater_days
        out["vol_90d_ann"] = realized_vol_annualized(ts, px)
        m30 = ts >= ts[-1] - 30
        out["ret_30d"] = float(px[-1] / px[m30][0] - 1) if m30.sum() > 1 else None
        out["payload"]["drawdowns"] = {str(w): st.as_dict() for w, st in dd.items()}

        # --- population --------------------------------------------------------------------
        pop_total = int(sum(v for k, v in x.pop_counts.items() if str(k).replace(".", "").isdigit())) if x.pop_counts else None
        out["pop_total"] = pop_total
        if x.pop_counts:
            tens = float(x.pop_counts.get("10", 0))
            out["pop_gem_rate"] = tens / pop_total if pop_total else None
        vel, mom_z = self._pop_velocity(x.pop_history)
        out["pop_velocity_30d"], out["pop_momentum_z"] = vel, mom_z

        # --- grading arbitrage + Kelly (raw -> graded) --------------------------------------
        grade_probs = None
        if x.pop_counts and x.raw_price:
            try:
                grade_probs = grade_distribution_from_pop(x.pop_counts, condition_tilt=p.condition_tilt)
            except ValueError:
                grade_probs = None
        if grade_probs and x.raw_price:
            arb = grading_arb(x.raw_price, x.slab_prices, grade_probs, grading_fee=p.grading_fee,
                              shipping=p.shipping, sell_fee_pct=p.sell_fee_pct)
            out["arb_spread"] = arb.spread
            out["payload"]["grading_arb"] = arb.as_dict()
            if x.arb_spread_history is not None and len(x.arb_spread_history) >= 20:
                sig = arb_signal(np.asarray(x.arb_spread_history, float), z_threshold=p.z_threshold)
                out["arb_spread_z"] = sig["z"] if np.isfinite(sig["z"]) else None
                out["payload"]["arb_signal"] = {k: v for k, v in sig.items() if k != "z_series"}
            k_acq = slab_acquisition_kelly(x.raw_price, x.slab_prices, grade_probs, grading_fee=p.grading_fee,
                                           shipping=p.shipping, sell_fee_pct=p.sell_fee_pct,
                                           multiplier=p.kelly_multiplier, cap=p.kelly_cap, bankroll=p.bankroll)
            out["payload"]["kelly_acquisition"] = k_acq.as_dict()

        # --- Kelly for holding the slab bought at market -----------------------------------
        hold = slab_holding_kelly(np.log(px), horizon_days=p.horizon_days,
                                  fair_log_price=float(np.log(fv.fair_value_now)) if fv.fair_value_now > 0 else None,
                                  reversion_half_life_days=max(fv.bandwidth_days, 14.0) if np.isfinite(fv.bandwidth_days) else 45.0,
                                  rf_annual=p.rf_annual, multiplier=p.kelly_multiplier, cap=p.kelly_cap)
        out["kelly_fraction"] = hold["f_star"]
        out["kelly_fraction_scaled"] = hold["f_scaled"]
        out["kelly_growth_rate"] = hold["growth_rate"]
        out["expected_return"] = hold["expected_return"]
        out["payload"]["kelly_holding"] = hold

        # --- JP/EN pairs -------------------------------------------------------------------
        if x.pair_log_prices is not None:
            a, b = x.pair_log_prices
            try:
                pr = pairs_spread(np.asarray(a, float), np.asarray(b, float))
                out["pairs_z"] = pr.z
                out["pairs_half_life_days"] = pr.half_life_days
                out["payload"]["pairs"] = {k: v for k, v in pr.as_dict().items() if k != "spread"}
            except ValueError:
                pass

        # --- Campbell–Shiller ---------------------------------------------------------------
        if x.cs_log_prices is not None and x.turnover_series is not None and x.pop_series is not None:
            d = scarcity_nostalgia_fundamental(x.turnover_series, x.pop_series, x.age_years, nostalgia_kappa=0.15)
            cs = campbell_shiller(np.asarray(x.cs_log_prices, float), d)
            if cs is not None:
                out["cs_fundamental_value"] = cs.fundamental_value
                out["cs_bubble_premium_pct"] = cs.bubble_premium_pct
                out["cs_bubble_share"] = 1.0 - cs.var_share_cf  # non-fundamental share (dr + bubble)
                cs_d = cs.as_dict()
                out["payload"]["campbell_shiller"] = {k: v for k, v in cs_d.items()
                                                      if k not in {"pd", "cf_component", "dr_component", "fundamental_log_price", "bubble", "speculative"}}
                out["payload"]["campbell_shiller"]["bubble_series"] = cs_d["speculative"][-60:]

        # --- liquidity + screener -----------------------------------------------------------
        liq = liquidity_score(trades_30d=x.trades_30d, days_since_last_trade=x.days_since_last_trade,
                              price_cv_30d=x.price_cv_30d, n_listings=x.n_listings, spread_pct=x.spread_pct)
        out["liquidity_score"] = liq["score"]
        out["payload"]["liquidity"] = liq
        out["kelly_adj_roi"] = kelly_adjusted_roi(out["kelly_fraction_scaled"], out["expected_return"])
        out["screener_score"] = screener_score(kelly_adj_roi=out["kelly_adj_roi"], liquidity=liq["score"],
                                               ev_gap_pct=out.get("ev_gap_pct"), arb_z=out.get("arb_spread_z"),
                                               bubble_premium_pct=out.get("cs_bubble_premium_pct"))
        return out

    # ----------------------------------------------------------------- sealed
    def analyze_sealed(self, x: SealedInputs) -> dict:
        p = self.p
        res = sealed_ev(x.price, x.n_packs, x.slots, bulk_value_per_pack=x.bulk_value_per_pack,
                        sell_fee_pct=p.sell_fee_pct, liquidity_haircut=x.liquidity_haircut)
        out: dict = {
            "ticker": x.ticker, "grade_key": "SEALED", "market_price": x.price,
            "sealed_ev": res.ev, "sealed_ev_ratio": res.ev_ratio, "sealed_p_profit": res.p_profit,
            "fair_value": res.ev, "ev_gap_pct": res.mispricing_pct,
            "payload": {"sealed_ev": res.as_dict()},
        }
        if x.ts_days is not None and x.prices is not None and len(x.prices) > 5:
            ts, px = np.asarray(x.ts_days, float), np.asarray(x.prices, float)
            dd = drawdown_profile(ts, px)
            for w, st in dd.items():
                out[f"dd_{w}d"] = st.max_drawdown
                out[f"recovery_{w}d_days"] = st.recovery_days
            out["underwater_days"] = dd[90].underwater_days
            out["vol_90d_ann"] = realized_vol_annualized(ts, px)
            out["payload"]["drawdowns"] = {str(w): st.as_dict() for w, st in dd.items()}
            hold = slab_holding_kelly(np.log(px), horizon_days=p.horizon_days, fair_log_price=float(np.log(res.ev)),
                                      reversion_half_life_days=120.0, rf_annual=p.rf_annual,
                                      multiplier=p.kelly_multiplier, cap=p.kelly_cap)
            out.update(kelly_fraction=hold["f_star"], kelly_fraction_scaled=hold["f_scaled"],
                       kelly_growth_rate=hold["growth_rate"], expected_return=hold["expected_return"])
            out["payload"]["kelly_holding"] = hold
        liq = liquidity_score(trades_30d=x.trades_30d, days_since_last_trade=x.days_since_last_trade,
                              price_cv_30d=x.price_cv_30d, n_listings=x.n_listings)
        out["liquidity_score"] = liq["score"]
        out["kelly_adj_roi"] = kelly_adjusted_roi(out.get("kelly_fraction_scaled"), out.get("expected_return"))
        out["screener_score"] = screener_score(kelly_adj_roi=out["kelly_adj_roi"], liquidity=liq["score"],
                                               ev_gap_pct=out["ev_gap_pct"])
        return out

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _pop_velocity(history: list[tuple[float, int]]) -> tuple[float | None, float | None]:
        """Submission velocity (slabs/day over trailing 30d) and its z-score vs prior 30d windows."""
        if len(history) < 2:
            return None, None
        h = np.array(sorted(history), dtype=float)
        t, n = h[:, 0], h[:, 1]
        now = t[-1]
        def rate(lo: float, hi: float) -> float | None:
            m = (t >= lo) & (t <= hi)
            if m.sum() < 2:
                return None
            return float((n[m][-1] - n[m][0]) / max(t[m][-1] - t[m][0], 1.0))
        v_now = rate(now - 30, now)
        prior = [r for r in (rate(now - 30 * (k + 1), now - 30 * k) for k in range(1, 7)) if r is not None]
        if v_now is None:
            return None, None
        if len(prior) >= 3 and np.std(prior) > 1e-9:
            return v_now, float((v_now - np.mean(prior)) / np.std(prior, ddof=1))
        return v_now, None
