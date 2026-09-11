"""Expected value of sealed product from pull rates and singles prices.

Model: a box is ``n_packs`` i.i.d. packs. Each pull *slot* (e.g. "SIR", "SAR", a specific chase
card) hits with probability ``prob_per_pack``; on a hit the card is drawn from the slot's member
values (uniform unless weights are supplied). Per-box hits are Binomial(n_packs, p). Value is
haircut for marketplace fees and a liquidity discount (chase singles are not instantly
liquid at last-sale prices). Analytic EV + variance are computed via the compound-binomial
formulas; the full P&L distribution (P(profit), percentiles) is estimated by Monte Carlo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class PullSlot:
    name: str
    prob_per_pack: float
    values: np.ndarray  # member card values (gross market)
    weights: np.ndarray | None = None

    def mean_value(self) -> float:
        w = self.weights if self.weights is not None else np.full(self.values.size, 1.0 / self.values.size)
        return float(np.dot(w, self.values))

    def second_moment(self) -> float:
        w = self.weights if self.weights is not None else np.full(self.values.size, 1.0 / self.values.size)
        return float(np.dot(w, self.values**2))


@dataclass(frozen=True)
class SealedEVResult:
    price: float
    ev: float
    ev_ratio: float  # ev / price
    mispricing: float  # ev - price
    mispricing_pct: float
    std: float
    p_profit: float
    percentiles: dict[str, float]
    slot_contributions: list[dict] = field(default_factory=list)
    n_packs: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def sealed_ev(
    price: float,
    n_packs: int,
    slots: list[PullSlot],
    *,
    bulk_value_per_pack: float = 0.35,
    sell_fee_pct: float = 0.1325,
    liquidity_haircut: float = 0.15,
    n_sims: int = 20_000,
    seed: int = 7,
) -> SealedEVResult:
    if price <= 0 or n_packs <= 0:
        raise ValueError("price and n_packs must be positive")
    net_mult = (1 - sell_fee_pct) * (1 - liquidity_haircut)
    rng = np.random.default_rng(seed)

    ev = n_packs * bulk_value_per_pack
    var = 0.0
    contributions = []
    total = np.full(n_sims, n_packs * bulk_value_per_pack, dtype=float)
    for s in slots:
        p = float(np.clip(s.prob_per_pack, 0.0, 1.0))
        m1, m2 = s.mean_value() * net_mult, s.second_moment() * net_mult**2
        hits_mean = n_packs * p
        slot_ev = hits_mean * m1
        # Var[sum_{k<=N} V_k] with N ~ Bin(n, p):  E[N] Var[V] + Var[N] E[V]^2
        slot_var = hits_mean * (m2 - m1**2) + n_packs * p * (1 - p) * m1**2
        ev += slot_ev
        var += slot_var
        contributions.append({"slot": s.name, "expected_hits": hits_mean, "ev": slot_ev,
                              "mean_card_value": s.mean_value(), "prob_per_pack": p})
        hits = rng.binomial(n_packs, p, size=n_sims)
        max_hits = int(hits.max()) if hits.size else 0
        if max_hits > 0:
            w = s.weights if s.weights is not None else None
            draws = rng.choice(s.values, size=(n_sims, max_hits), replace=True, p=w) * net_mult
            mask = np.arange(max_hits)[None, :] < hits[:, None]
            total += (draws * mask).sum(axis=1)

    pnl = total - price
    pct = {str(q): float(np.percentile(total, q)) for q in (5, 25, 50, 75, 95)}
    return SealedEVResult(
        price=price, ev=ev, ev_ratio=ev / price, mispricing=ev - price, mispricing_pct=(ev - price) / price,
        std=float(np.sqrt(var)), p_profit=float(np.mean(pnl > 0)), percentiles=pct,
        slot_contributions=contributions, n_packs=n_packs,
    )


def ev_matrix(rows: list[dict]) -> list[dict]:
    """rows: [{"ticker":..., "price":..., "n_packs":..., "slots":[PullSlot], **kw}] -> ranked by mispricing_pct."""
    out = []
    for r in rows:
        res = sealed_ev(r["price"], r["n_packs"], r["slots"], **{k: v for k, v in r.items()
                        if k in {"bulk_value_per_pack", "sell_fee_pct", "liquidity_haircut", "n_sims", "seed"}})
        d = res.as_dict()
        d["ticker"] = r.get("ticker")
        out.append(d)
    out.sort(key=lambda d: d["mispricing_pct"], reverse=True)
    return out
