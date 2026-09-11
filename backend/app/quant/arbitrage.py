"""Grading arbitrage and JP/EN pairs-trading statistics.

Grading arb: the raw card plus grading cost is a synthetic long on the *distribution* of
graded outcomes. We track the spread between the expected graded value and the all-in raw
cost, standardize it against its own history, and fire when |z| breaches a threshold.

Pairs: log-price spread between a Japanese print and its English equivalent (or two grades of
the same card). Hedge ratio by OLS, mean-reversion speed by AR(1) on the spread
(Ornstein–Uhlenbeck discretization), half-life = -ln 2 / lambda.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class GradingArbResult:
    raw_price: float
    all_in_cost: float
    expected_graded_value: float  # net of selling fees
    spread: float  # EGV - cost
    spread_pct: float  # spread / cost
    breakeven_gem_rate: float | None  # P(top grade) needed for spread == 0
    per_grade: dict[str, dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def grading_arb(
    raw_price: float,
    slab_prices: Mapping[str, float],
    grade_probs: Mapping[str, float],
    *,
    grading_fee: float,
    shipping: float,
    sell_fee_pct: float,
    top_grade: str = "10",
) -> GradingArbResult:
    cost = raw_price + grading_fee + shipping
    per_grade: dict[str, dict[str, float]] = {}
    egv = 0.0
    for g, p in grade_probs.items():
        gross = slab_prices.get(g, raw_price)
        net = gross * (1 - sell_fee_pct)
        egv += p * net
        per_grade[g] = {"prob": float(p), "gross": float(gross), "net": float(net)}
    spread = egv - cost

    breakeven = None
    if top_grade in per_grade and per_grade[top_grade]["prob"] < 1.0:
        v_top = per_grade[top_grade]["net"]
        p_top = per_grade[top_grade]["prob"]
        others = [(g, d) for g, d in per_grade.items() if g != top_grade]
        rest_mass = sum(d["prob"] for _, d in others)
        v_rest = sum(d["prob"] * d["net"] for _, d in others) / rest_mass if rest_mass > 0 else 0.0
        # cost = p * v_top + (1 - p) * v_rest  =>  p = (cost - v_rest) / (v_top - v_rest)
        if abs(v_top - v_rest) > 1e-9:
            breakeven = float(np.clip((cost - v_rest) / (v_top - v_rest), 0.0, 1.0))
        _ = p_top
    return GradingArbResult(
        raw_price=raw_price,
        all_in_cost=cost,
        expected_graded_value=egv,
        spread=spread,
        spread_pct=spread / cost if cost else 0.0,
        breakeven_gem_rate=breakeven,
        per_grade=per_grade,
    )


def rolling_zscore(x: np.ndarray, window: int = 90, min_periods: int = 20) -> tuple[np.ndarray, float, float]:
    """Trailing z-score of the last observation vs the prior ``window`` values (no look-ahead)."""
    x = np.asarray(x, dtype=float)
    z = np.full(x.shape, np.nan)
    for i in range(x.size):
        lo = max(0, i - window)
        hist = x[lo:i]
        if hist.size >= min_periods:
            sd = hist.std(ddof=1)
            if sd > 1e-12:
                z[i] = (x[i] - hist.mean()) / sd
    hist = x[max(0, x.size - 1 - window) : -1] if x.size > 1 else x
    return z, float(hist.mean()) if hist.size else float("nan"), float(hist.std(ddof=1)) if hist.size > 1 else float("nan")


def arb_signal(spread_history: np.ndarray, *, z_threshold: float = 2.0, window: int = 90) -> dict:
    z, mean, sd = rolling_zscore(spread_history, window=window)
    z_now = float(z[-1]) if z.size and np.isfinite(z[-1]) else float("nan")
    direction = "flat"
    if np.isfinite(z_now):
        if z_now >= z_threshold:
            direction = "long"  # grade-and-flip is unusually rich
        elif z_now <= -z_threshold:
            direction = "short"  # sell slabs / buy raw: graded premium collapsed
    return {"z": z_now, "mean": mean, "std": sd, "direction": direction, "z_series": z}


@dataclass(frozen=True)
class PairsResult:
    alpha: float
    beta: float
    spread: np.ndarray
    z: float
    mean: float
    std: float
    lam: float  # AR(1) mean-reversion coefficient on the spread (negative => reverting)
    half_life_days: float | None
    r2: float

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["spread"] = self.spread.tolist()
        return d


def pairs_spread(log_a: np.ndarray, log_b: np.ndarray, dt_days: float = 1.0) -> PairsResult:
    """Spread s_t = log_a - (alpha + beta log_b); AR(1): ds_t = lam * s_{t-1} + eps."""
    a = np.asarray(log_a, dtype=float)
    b = np.asarray(log_b, dtype=float)
    if a.shape != b.shape or a.size < 8:
        raise ValueError("need >= 8 aligned observations")
    X = np.column_stack([np.ones_like(b), b])
    coef, *_ = np.linalg.lstsq(X, a, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    s = a - X @ coef
    ss_res = float(np.sum(s**2))
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    ds = np.diff(s)
    lag = s[:-1]
    lam = float(np.dot(lag, ds) / np.dot(lag, lag)) if np.dot(lag, lag) > 0 else 0.0
    half_life = float(-np.log(2) / lam * dt_days) if lam < 0 else None
    sd = float(s.std(ddof=1))
    z = float((s[-1] - s.mean()) / sd) if sd > 1e-12 else 0.0
    return PairsResult(alpha=alpha, beta=beta, spread=s, z=z, mean=float(s.mean()), std=sd,
                       lam=lam, half_life_days=half_life, r2=float(r2))
