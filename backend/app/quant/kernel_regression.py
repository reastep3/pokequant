"""Non-parametric fair-value trendline for sparse, illiquid sales data.

Local-linear (LL) kernel regression in log-price space with a Gaussian kernel:

    min_{b0,b1} sum_i K_h(t_i - t) * w_i * (y_i - b0 - b1 (t_i - t))^2

LL is preferred over Nadaraya–Watson because it is unbiased at the boundary — and the
boundary (today) is exactly where the fair value matters. Bandwidth is chosen by
leave-one-out cross-validation over multiples of Silverman's rule with a floor in days.
Outliers (mis-listed lots, damaged slabs sold as gems) are down-weighted by two rounds of
Tukey biweight re-weighting. Standard errors come from the equivalent-kernel weights:
Var[m_hat(t)] = sigma^2 * sum_i l_i(t)^2.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SQRT_2PI = float(np.sqrt(2 * np.pi))


@dataclass(frozen=True)
class FairValueResult:
    grid_t: np.ndarray
    fitted: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    fair_value_now: float
    fair_lo_now: float
    fair_hi_now: float
    bandwidth_days: float
    resid_std: float
    n_obs: int
    z_market_vs_fair: float | None
    method: str

    def as_dict(self) -> dict:
        return {
            "grid_t": self.grid_t.tolist(), "fitted": self.fitted.tolist(),
            "lower": self.lower.tolist(), "upper": self.upper.tolist(),
            "fair_value_now": self.fair_value_now, "fair_lo_now": self.fair_lo_now,
            "fair_hi_now": self.fair_hi_now, "bandwidth_days": self.bandwidth_days,
            "resid_std": self.resid_std, "n_obs": self.n_obs,
            "z_market_vs_fair": self.z_market_vs_fair, "method": self.method,
        }


def gaussian_kernel(u: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * u * u) / SQRT_2PI


def silverman_bandwidth(t: np.ndarray) -> float:
    n = t.size
    sd = float(np.std(t, ddof=1)) if n > 1 else 1.0
    q75, q25 = np.percentile(t, [75, 25])
    iqr = float(q75 - q25)
    a = min(sd, iqr / 1.34) if iqr > 0 else sd
    return max(1e-6, 1.06 * a * n ** (-1 / 5))


def _ll_weights(t: np.ndarray, t0: float, h: float, w: np.ndarray) -> np.ndarray:
    """Equivalent-kernel weights l(t0) such that m_hat(t0) = l' y (local linear)."""
    u = (t - t0) / h
    k = gaussian_kernel(u) * w
    s0, s1, s2 = k.sum(), (k * (t - t0)).sum(), (k * (t - t0) ** 2).sum()
    denom = s0 * s2 - s1**2
    if denom <= 1e-14:  # degenerate: fall back to Nadaraya–Watson weights
        return k / k.sum() if k.sum() > 0 else np.full_like(k, 1.0 / k.size)
    return k * (s2 - s1 * (t - t0)) / denom


def local_linear(t: np.ndarray, y: np.ndarray, grid: np.ndarray, h: float, w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Returns (fitted, sum of squared equivalent-kernel weights) on ``grid``."""
    w = np.ones_like(t) if w is None else w
    fit = np.empty(grid.size)
    l2 = np.empty(grid.size)
    for j, g in enumerate(grid):
        L = _ll_weights(t, float(g), h, w)
        fit[j] = float(L @ y)
        l2[j] = float(L @ L)
    return fit, l2


def nadaraya_watson(t: np.ndarray, y: np.ndarray, grid: np.ndarray, h: float, w: np.ndarray | None = None) -> np.ndarray:
    w = np.ones_like(t) if w is None else w
    out = np.empty(grid.size)
    for j, g in enumerate(grid):
        k = gaussian_kernel((t - g) / h) * w
        out[j] = float(k @ y / k.sum()) if k.sum() > 0 else np.nan
    return out


def loocv_bandwidth(t: np.ndarray, y: np.ndarray, candidates: np.ndarray, w: np.ndarray | None = None) -> float:
    w = np.ones_like(t) if w is None else w
    best_h, best_err = float(candidates[0]), np.inf
    n = t.size
    for h in candidates:
        err = 0.0
        for i in range(n):
            mask = np.arange(n) != i
            L = _ll_weights(t[mask], float(t[i]), h, w[mask])
            err += w[i] * (y[i] - float(L @ y[mask])) ** 2
        if err < best_err:
            best_err, best_h = err, float(h)
    return best_h


def fair_value_trendline(
    ts_days: np.ndarray,
    prices: np.ndarray,
    *,
    now_day: float | None = None,
    market_price: float | None = None,
    grid_n: int = 120,
    bandwidth_days: float | None = None,
    min_bandwidth_days: float = 7.0,
    log_space: bool = True,
    robust_iters: int = 2,
    min_obs: int = 5,
    ci_z: float = 1.96,
) -> FairValueResult:
    t = np.asarray(ts_days, dtype=float)
    p = np.asarray(prices, dtype=float)
    order = np.argsort(t)
    t, p = t[order], p[order]
    now_day = float(t[-1]) if now_day is None else float(now_day)
    n = t.size
    y = np.log(p) if log_space else p
    inv = (lambda v: np.exp(v)) if log_space else (lambda v: v)

    if n < min_obs:
        # Not enough sales for a kernel fit: robust fallback to the median of what we have.
        med = float(np.median(y))
        sd = float(np.std(y, ddof=1)) if n > 1 else (0.25 if log_space else 0.25 * abs(med))
        grid = np.linspace(t[0] if n else now_day, now_day, grid_n)
        fit = np.full(grid_n, med)
        z = ((np.log(market_price) if log_space else market_price) - med) / sd if (market_price and sd > 0) else None
        return FairValueResult(
            grid_t=grid, fitted=inv(fit), lower=inv(fit - ci_z * sd), upper=inv(fit + ci_z * sd),
            fair_value_now=float(inv(med)), fair_lo_now=float(inv(med - ci_z * sd)), fair_hi_now=float(inv(med + ci_z * sd)),
            bandwidth_days=float("nan"), resid_std=sd, n_obs=n, z_market_vs_fair=z, method="median_fallback",
        )

    w = np.ones(n)
    if bandwidth_days is None:
        h0 = max(silverman_bandwidth(t), min_bandwidth_days)
        cands = np.array([0.5, 0.75, 1.0, 1.5, 2.0, 3.0]) * h0
        cands = cands[cands >= min_bandwidth_days] if np.any(cands >= min_bandwidth_days) else np.array([min_bandwidth_days])
        h = loocv_bandwidth(t, y, cands)
    else:
        h = float(bandwidth_days)

    # Robust re-weighting (Tukey biweight on scaled residuals).
    for _ in range(max(0, robust_iters)):
        fit_at_obs, _ = local_linear(t, y, t, h, w)
        r = y - fit_at_obs
        s = 1.4826 * float(np.median(np.abs(r - np.median(r)))) or float(np.std(r)) or 1e-6
        u = r / (6.0 * s)
        w = np.where(np.abs(u) < 1, (1 - u**2) ** 2, 0.0)
        if w.sum() < 3:  # never let robustification erase the sample
            w = np.ones(n)
            break

    grid = np.linspace(t[0], now_day, grid_n)
    fit_grid, l2_grid = local_linear(t, y, grid, h, w)
    fit_obs, _ = local_linear(t, y, t, h, w)
    resid = y - fit_obs
    dof = max(1.0, float(w.sum()) - 2.0)
    sigma2 = float(np.sum(w * resid**2) / dof)
    se = np.sqrt(sigma2 * l2_grid)
    lower, upper = fit_grid - ci_z * se, fit_grid + ci_z * se
    fv_now, lo_now, hi_now = float(fit_grid[-1]), float(lower[-1]), float(upper[-1])
    resid_std = float(np.sqrt(sigma2))
    z = None
    if market_price is not None and resid_std > 0:
        mp = np.log(market_price) if log_space else market_price
        z = float((mp - fv_now) / resid_std)
    return FairValueResult(
        grid_t=grid, fitted=inv(fit_grid), lower=inv(lower), upper=inv(upper),
        fair_value_now=float(inv(fv_now)), fair_lo_now=float(inv(lo_now)), fair_hi_now=float(inv(hi_now)),
        bandwidth_days=h, resid_std=resid_std, n_obs=n, z_market_vs_fair=z, method="local_linear_robust",
    )
