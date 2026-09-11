"""Kelly-criterion capital allocation for slab acquisitions.

Three estimators, used in different situations:

* ``kelly_discrete``      – exact growth-optimal fraction for a discrete outcome distribution.
                            Used for *raw -> graded* bets where the grade outcome is the
                            random variable (probabilities from population reports).
* ``kelly_continuous``    – Merton/Kelly fraction f* = (mu - r_f) / sigma^2 for an asset with
                            (approximately) log-normal returns. Used for *already graded*
                            slabs where the bet is on price drift + mean reversion to fair value.
* ``kelly_portfolio``     – long-only, gross-constrained multivariate Kelly f = Sigma^-1 (mu - r_f)
                            solved as a small QP so allocations across many slabs are coherent.

All fractions are of *bankroll*. ``multiplier`` (default half-Kelly) and ``cap`` implement the
usual practitioner haircut for estimation error in mu/sigma.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq, minimize

GRADE_NUMERIC = {
    "10": 10.0, "9.5": 9.5, "9": 9.0, "8.5": 8.5, "8": 8.0, "7.5": 7.5, "7": 7.0,
    "6.5": 6.5, "6": 6.0, "5.5": 5.5, "5": 5.0, "4": 4.0, "3": 3.0, "2": 2.0, "1.5": 1.5, "1": 1.0,
}


@dataclass(frozen=True)
class Outcome:
    label: str
    prob: float
    payoff: float  # net proceeds
    ret: float  # payoff / cost - 1


@dataclass(frozen=True)
class KellyResult:
    f_star: float
    f_scaled: float
    growth_rate: float  # E[log(1 + f* r)] per bet
    expected_return: float
    variance: float
    win_prob: float
    allocation: float
    f_max: float
    outcomes: list[Outcome] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "outcomes"}
        d["outcomes"] = [o.__dict__ for o in self.outcomes]
        return d


def grade_distribution_from_pop(
    grade_counts: Mapping[str, int | float],
    prior: Mapping[str, float] | None = None,
    alpha: float = 5.0,
    condition_tilt: float = 0.0,
) -> dict[str, float]:
    """Dirichlet-smoothed grade probabilities from a population report.

    Pop reports are a *selected* sample (submitters cherry-pick, 9s get cracked and resubmitted),
    so the raw frequencies overstate gem rates for a random raw card. ``condition_tilt`` is a
    log-odds tilt per grade point: negative values shift mass to lower grades (unknown raw
    condition), positive values toward higher grades (pack-fresh / pre-screened).
    """
    labels = [g for g in GRADE_NUMERIC if g in grade_counts]
    if not labels:
        raise ValueError("no numeric grade keys in pop report")
    counts = np.array([float(grade_counts[g]) for g in labels])
    if prior is None:
        pr = np.full(len(labels), 1.0 / len(labels))
    else:
        pr = np.array([float(prior.get(g, 0.0)) for g in labels])
        pr = pr / pr.sum() if pr.sum() > 0 else np.full(len(labels), 1.0 / len(labels))
    p = (counts + alpha * pr) / (counts.sum() + alpha)
    if condition_tilt:
        g = np.array([GRADE_NUMERIC[x] for x in labels])
        w = np.exp(condition_tilt * (g - np.average(g, weights=p)))
        p = p * w / np.sum(p * w)
    return dict(zip(labels, p.tolist(), strict=True))


def _feasible_max_fraction(returns: np.ndarray, hard_cap: float = 1.0) -> float:
    r_min = float(returns.min())
    if r_min >= 0:
        return hard_cap
    return min(hard_cap, -1.0 / r_min * (1 - 1e-6))


def kelly_discrete(
    returns: np.ndarray,
    probs: np.ndarray,
    *,
    multiplier: float = 0.5,
    cap: float = 0.25,
    bankroll: float = 1.0,
    labels: list[str] | None = None,
    payoffs: np.ndarray | None = None,
) -> KellyResult:
    """Growth-optimal fraction for a discrete return distribution.

    Maximizes G(f) = sum_i p_i log(1 + f r_i) over f in [0, f_max]. G is strictly concave so
    the root of G'(f) = sum_i p_i r_i / (1 + f r_i) is the unique optimum.
    """
    r = np.asarray(returns, dtype=float)
    p = np.asarray(probs, dtype=float)
    if r.shape != p.shape or r.ndim != 1:
        raise ValueError("returns and probs must be 1-D and aligned")
    p = p / p.sum()
    edge = float(np.dot(p, r))
    var = float(np.dot(p, (r - edge) ** 2))
    f_max = _feasible_max_fraction(r)
    note = ""

    def g_prime(f: float) -> float:
        return float(np.sum(p * r / (1.0 + f * r)))

    if edge <= 0:
        f_star, note = 0.0, "non-positive edge"
    elif g_prime(f_max) > 0:
        f_star, note = f_max, "corner solution (all outcomes non-negative or f capped by ruin bound)"
    else:
        f_star = float(brentq(g_prime, 0.0, f_max, xtol=1e-10))

    growth = float(np.sum(p * np.log1p(f_star * r))) if f_star > 0 else 0.0
    f_scaled = min(cap, multiplier * f_star)
    labels = labels or [f"o{i}" for i in range(len(r))]
    pay = payoffs if payoffs is not None else (1 + r)
    outcomes = [
        Outcome(label=labels[i], prob=float(p[i]), payoff=float(pay[i]), ret=float(r[i]))
        for i in range(len(r))
    ]
    return KellyResult(
        f_star=f_star,
        f_scaled=f_scaled,
        growth_rate=growth,
        expected_return=edge,
        variance=var,
        win_prob=float(p[r > 0].sum()),
        allocation=f_scaled * bankroll,
        f_max=f_max,
        outcomes=outcomes,
        note=note,
    )


def slab_acquisition_kelly(
    raw_price: float,
    slab_prices: Mapping[str, float],
    grade_probs: Mapping[str, float],
    *,
    grading_fee: float,
    shipping: float,
    sell_fee_pct: float,
    multiplier: float = 0.5,
    cap: float = 0.25,
    bankroll: float = 1.0,
) -> KellyResult:
    """Kelly sizing for 'buy raw, grade, sell slab'.

    Grades without an observed slab price are valued at the raw price (a low-grade slab is
    worth roughly its raw equivalent) so the downside is never silently dropped.
    """
    cost = raw_price + grading_fee + shipping
    if cost <= 0:
        raise ValueError("cost must be positive")
    labels, probs, payoffs = [], [], []
    for grade, prob in grade_probs.items():
        if prob <= 0:
            continue
        gross = slab_prices.get(grade)
        if gross is None:
            gross = raw_price
        labels.append(grade)
        probs.append(prob)
        payoffs.append(gross * (1.0 - sell_fee_pct))
    payoffs_arr = np.array(payoffs)
    returns = payoffs_arr / cost - 1.0
    return kelly_discrete(
        returns, np.array(probs), multiplier=multiplier, cap=cap, bankroll=bankroll,
        labels=labels, payoffs=payoffs_arr,
    )


def kelly_continuous(
    mu: float, sigma2: float, *, rf: float = 0.0, multiplier: float = 0.5, cap: float = 0.25
) -> tuple[float, float, float]:
    """Merton fraction for log-normal returns. Returns (f_star, f_scaled, growth_rate)."""
    if sigma2 <= 0:
        return 0.0, 0.0, 0.0
    f_star = max(0.0, (mu - rf) / sigma2)
    growth = rf + f_star * (mu - rf) - 0.5 * f_star**2 * sigma2
    return f_star, min(cap, multiplier * f_star), growth


def slab_holding_kelly(
    log_prices: np.ndarray,
    *,
    horizon_days: int = 90,
    fair_log_price: float | None = None,
    reversion_half_life_days: float | None = None,
    rf_annual: float = 0.0,
    multiplier: float = 0.5,
    cap: float = 0.25,
    periods_per_year: float = 365.0,
) -> dict:
    """Kelly for buying an already-graded slab at market and holding ``horizon_days``.

    mu_h = drift * h + expected mean-reversion toward fair value; sigma_h^2 = realized daily
    variance * h (i.i.d. scaling; conservative for mean-reverting series).
    """
    lp = np.asarray(log_prices, dtype=float)
    if lp.size < 10:
        return {"f_star": 0.0, "f_scaled": 0.0, "mu_h": 0.0, "sigma2_h": 0.0, "growth_rate": 0.0}
    d = np.diff(lp)
    drift_daily = float(np.mean(d))
    var_daily = float(np.var(d, ddof=1))
    mu_h = drift_daily * horizon_days
    if fair_log_price is not None and reversion_half_life_days:
        gap = fair_log_price - lp[-1]
        mu_h += gap * (1.0 - 0.5 ** (horizon_days / reversion_half_life_days))
    sigma2_h = var_daily * horizon_days
    rf_h = rf_annual * horizon_days / periods_per_year
    f_star, f_scaled, growth = kelly_continuous(mu_h, sigma2_h, rf=rf_h, multiplier=multiplier, cap=cap)
    return {
        "f_star": f_star, "f_scaled": f_scaled, "mu_h": mu_h, "sigma2_h": sigma2_h,
        "growth_rate": growth, "expected_return": float(np.expm1(mu_h + 0.5 * sigma2_h)),
    }


def kelly_portfolio(
    mu: np.ndarray,
    cov: np.ndarray,
    *,
    rf: float = 0.0,
    max_gross: float = 1.0,
    long_only: bool = True,
    multiplier: float = 0.5,
) -> np.ndarray:
    """Multivariate Kelly: maximize f'(mu - rf) - 0.5 f'Sigma f s.t. f >= 0, sum f <= max_gross."""
    mu = np.asarray(mu, dtype=float)
    cov = np.asarray(cov, dtype=float)
    n = mu.size
    excess = mu - rf
    unconstrained = np.linalg.solve(cov + 1e-9 * np.eye(n), excess)
    if not long_only and unconstrained.sum() <= max_gross:
        return multiplier * unconstrained

    def neg_growth(f: np.ndarray) -> float:
        return -(f @ excess - 0.5 * f @ cov @ f)

    def grad(f: np.ndarray) -> np.ndarray:
        return -(excess - cov @ f)

    cons = [{"type": "ineq", "fun": lambda f: max_gross - f.sum(), "jac": lambda f: -np.ones(n)}]
    bounds = [(0.0, max_gross)] * n if long_only else [(-max_gross, max_gross)] * n
    x0 = np.clip(unconstrained, 0, None)
    if x0.sum() > max_gross:
        x0 = x0 * max_gross / x0.sum()
    res = minimize(neg_growth, x0, jac=grad, bounds=bounds, constraints=cons, method="SLSQP")
    f = res.x if res.success else x0
    return multiplier * np.where(np.abs(f) < 1e-10, 0.0, f)
