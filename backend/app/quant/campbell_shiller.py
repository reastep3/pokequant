r"""Campbell–Shiller log-linear present-value decomposition for collectibles.

Collectibles pay no cash dividend, so the "fundamental flow" d_t is a proxy for the scarcity /
nostalgia service flow: by default log(turnover_t / population_t) — demand intensity per unit
of graded supply — optionally with an age-based nostalgia kernel. Only the *dynamics* of the
proxy are identified; its level is pinned by an explicit fundamental-yield assumption
(``fundamental_yield_annual``), which sets the linearization constant rho = 1 / (1 + D/P).

Log-linearization (Campbell & Shiller 1988):
    r_{t+1} = kappa + rho * pd_{t+1} - pd_t + Δd_{t+1},   pd_t = p_t - d_t
    rho = 1 / (1 + exp(mean(d - p))),   kappa = -log(rho) - (1 - rho) log(1/rho - 1)
Iterating forward:
    pd_t = const + Σ_j rho^j E_t[Δd_{t+1+j}] - Σ_j rho^j E_t[r_{t+1+j}] + lim_T rho^T E_t[pd_{t+T}]
                   \___ cash-flow (cf) ___/   \___ discount-rate (dr) ___/   \___ rational bubble ___/

Two estimators of the conditional expectations:

* ``longrun`` (default, Cochrane 2011 "long-run" regressions) — three predictive slopes on the
  demeaned ratio: Δd_{t+1} = b_d pd_t, r^p_{t+1} = b_r pd_t, pd_{t+1} = φ pd_t, with r^p the
  realized *price* log return (what a holder earns). Then, exactly,
      cf_t = b_d/(1-ρφ) · pd_t,   dr_t = -b_r/(1-ρφ) · pd_t,   bubble_t = φ(1-ρ)/(1-ρφ) · pd_t,
  and the three variance shares sum to one. Three parameters => stable in short samples.
  The bubble share is the persistence-of-the-ratio term: it goes to 1 as φ -> 1/ρ (explosive
  ratio, the Diba–Grossman / Phillips–Shi–Yu bubble condition) and to 0 for a white-noise ratio.
* ``var`` — unrestricted VAR(1) on [Δd, pd, r^p] (ridge-regularized, spectral radius capped)
  with E_t Σ rho^j z_{t+1+j} = A (I - ρA)^{-1} z_t. Only used for long samples (>= 60 obs).

Reported headline: the **speculative premium** = deviation of pd from the constant-required-return
fundamental = dr_t + bubble_t (everything not justified by expected fundamental growth).
Fundamental value F_t = exp(d_t + mean(pd) + cf_t); premium_pct = P_t / F_t - 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class CSResult:
    rho: float
    kappa: float
    method: str
    pd: np.ndarray
    cf_component: np.ndarray  # demeaned, log units
    dr_component: np.ndarray
    bubble: np.ndarray  # rational-bubble (persistence) component, demeaned
    speculative: np.ndarray  # dr + bubble
    fundamental_log_price: np.ndarray
    bubble_premium_pct: float  # speculative premium at T: P_T / F_T - 1
    rational_bubble_premium_pct: float
    fundamental_value: float  # F_T in price units
    var_share_cf: float
    var_share_dr: float
    var_share_bubble: float
    n_obs: int
    coefs: dict
    stable: bool

    def as_dict(self) -> dict:
        return {
            "rho": self.rho, "kappa": self.kappa, "method": self.method, "pd": self.pd.tolist(),
            "cf_component": self.cf_component.tolist(), "dr_component": self.dr_component.tolist(),
            "bubble": self.bubble.tolist(), "speculative": self.speculative.tolist(),
            "fundamental_log_price": self.fundamental_log_price.tolist(),
            "bubble_premium_pct": self.bubble_premium_pct,
            "rational_bubble_premium_pct": self.rational_bubble_premium_pct,
            "fundamental_value": self.fundamental_value,
            "var_share_cf": self.var_share_cf, "var_share_dr": self.var_share_dr,
            "var_share_bubble": self.var_share_bubble, "n_obs": self.n_obs, "coefs": self.coefs, "stable": self.stable,
        }


def scarcity_nostalgia_fundamental(
    turnover: np.ndarray, population: np.ndarray, age_years: np.ndarray | None = None, nostalgia_kappa: float = 0.0
) -> np.ndarray:
    """d_t = log((turnover_t + 1) / (pop_t + 1)) + kappa * log(1 + age). Returns log flow (level arbitrary)."""
    to = np.asarray(turnover, dtype=float)
    pop = np.asarray(population, dtype=float)
    d = np.log1p(to) - np.log1p(pop)
    if age_years is not None and nostalgia_kappa:
        d = d + nostalgia_kappa * np.log1p(np.asarray(age_years, dtype=float))
    return d


def log_linearize(p: np.ndarray, d: np.ndarray) -> tuple[float, float]:
    rho = 1.0 / (1.0 + np.exp(np.mean(d - p)))
    rho = float(np.clip(rho, 0.5, 0.9999))
    kappa = -np.log(rho) - (1 - rho) * np.log(1 / rho - 1)
    return rho, float(kappa)


def calibrate_level(p: np.ndarray, d: np.ndarray, yield_per_period: float) -> np.ndarray:
    """Shift d so that mean(D/P) = yield_per_period (the proxy's level is not identified)."""
    return d + (np.mean(p - d) + np.log(yield_per_period))


def var1_fit(Z: np.ndarray, ridge: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Z_{t+1} = A Z_t + e (Z already demeaned). Returns (A, residuals)."""
    X, Y = Z[:-1], Z[1:]
    k = X.shape[1]
    XtX = X.T @ X + ridge * np.eye(k)
    A = np.linalg.solve(XtX, X.T @ Y).T
    return A, Y - X @ A.T


def _slope(y: np.ndarray, x: np.ndarray, ridge: float = 0.0) -> tuple[float, float]:
    """OLS slope through the origin and its standard error (x, y demeaned)."""
    xx = float(x @ x)
    if xx <= 0 or x.size < 4:
        return 0.0, float("inf")
    b = float(x @ y / (xx + ridge))
    resid = y - b * x
    se = float(np.sqrt((resid @ resid) / (x.size - 1) / xx))
    return b, se


def _shrink(b: float, se: float, cap: float) -> float:
    """James–Stein-style shrinkage toward 0: keep b * t^2/(1+t^2), then hard-cap |b| <= cap.

    Short, noisy collectible samples make the long-run multiplier 1/(1-rho*phi) amplify slope
    noise by 10-30x; shrinking by signal-to-noise keeps the decomposition inside credible bounds
    (a long-run share beyond +-cap of the price/fundamental ratio is not an estimate, it's noise).
    """
    if not np.isfinite(se) or se <= 0:
        return 0.0
    t2 = (b / se) ** 2
    return float(np.clip(b * t2 / (1.0 + t2), -cap, cap))


def campbell_shiller(
    log_price: np.ndarray,
    log_fundamental: np.ndarray,
    *,
    method: Literal["auto", "longrun", "var"] = "auto",
    fundamental_yield_annual: float | None = 0.08,
    periods_per_year: int = 12,
    min_obs: int = 16,
    max_rho_phi: float = 0.97,
    max_share: float = 2.0,
) -> CSResult | None:
    p = np.asarray(log_price, dtype=float)
    d = np.asarray(log_fundamental, dtype=float)
    if p.shape != d.shape or p.size < min_obs:
        return None
    if fundamental_yield_annual:
        d = calibrate_level(p, d, fundamental_yield_annual / periods_per_year)
    pd = p - d
    rho, kappa = log_linearize(p, d)
    dd, rp = np.diff(d), np.diff(p)
    pd_ = pd[1:]
    mu_pd = float(pd_.mean())
    pdm = pd_ - mu_pd
    n = pd_.size
    if method == "auto":
        method = "var" if n >= 60 else "longrun"
    stable = True
    coefs: dict

    if method == "longrun":
        x = pdm[:-1]
        b_d, se_d = _slope(dd[1:] - dd[1:].mean(), x)
        b_r, se_r = _slope(rp[1:] - rp[1:].mean(), x)
        phi, _ = _slope(pdm[1:], x)
        if rho * phi > max_rho_phi:
            phi, stable = max_rho_phi / rho, False
        phi = max(phi, 0.0)
        m = 1.0 / (1.0 - rho * phi)
        # Long-run shares: shrink the *multiplied* coefficient by its signal-to-noise, cap at +-max_share.
        s_cf = _shrink(b_d * m, se_d * m, max_share)
        s_dr = _shrink(-b_r * m, se_r * m, max_share)
        s_bub = 1.0 - s_cf - s_dr
        cf, dr, bub = s_cf * pdm, s_dr * pdm, s_bub * pdm
        coefs = {"b_d": b_d, "se_d": se_d, "b_r": b_r, "se_r": se_r, "phi": phi, "multiplier": m}
    else:
        Z = np.column_stack([dd, pdm, rp])
        Zd = Z - Z.mean(axis=0)
        ridge = 0.05 * float(np.trace(Zd[:-1].T @ Zd[:-1])) / 3
        A, _ = var1_fit(Zd, ridge=ridge)
        rad = float(np.max(np.abs(np.linalg.eigvals(A))))
        if rho * rad > 0.9:
            A, stable = A * (0.9 / (rho * rad)), False
        M = A @ np.linalg.solve(np.eye(3) - rho * A, np.eye(3))
        es = Zd @ M.T
        cf, dr = es[:, 0], -es[:, 2]
        for comp in (cf, dr):  # same credibility bound as the long-run estimator
            scale = float(np.std(comp) / (np.std(pdm) + 1e-12))
            if scale > max_share:
                comp *= max_share / scale
                stable = False
        bub = pdm - cf - dr
        coefs = {"A": A.tolist(), "spectral_radius": rad}
    spec = dr + bub
    fundamental_log_price = d[1:] + mu_pd + cf
    var_pd = float(np.var(pdm))

    def share(xv: np.ndarray) -> float:
        return float(np.mean(pdm * xv) / var_pd) if var_pd > 1e-12 else float("nan")

    return CSResult(
        rho=rho, kappa=kappa, method=method, pd=pd_, cf_component=cf, dr_component=dr, bubble=bub, speculative=spec,
        fundamental_log_price=fundamental_log_price,
        bubble_premium_pct=float(np.expm1(spec[-1])), rational_bubble_premium_pct=float(np.expm1(bub[-1])),
        fundamental_value=float(np.exp(fundamental_log_price[-1])),
        var_share_cf=share(cf), var_share_dr=share(dr), var_share_bubble=share(bub),
        n_obs=int(n), coefs=coefs, stable=stable,
    )
