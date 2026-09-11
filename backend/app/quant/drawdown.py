"""Drawdown, recovery-time and underwater statistics over trailing windows."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DrawdownStats:
    window_days: int
    max_drawdown: float  # negative fraction, e.g. -0.23
    current_drawdown: float
    peak_day: float
    trough_day: float
    recovery_days: int | None  # trough -> first close back at/above the pre-drawdown peak
    underwater_days: int  # days since the running max was last touched

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def drawdown_series(prices: np.ndarray) -> np.ndarray:
    p = np.asarray(prices, dtype=float)
    running_max = np.maximum.accumulate(p)
    return p / running_max - 1.0


def window_stats(ts_days: np.ndarray, prices: np.ndarray, window_days: int) -> DrawdownStats:
    t = np.asarray(ts_days, dtype=float)
    p = np.asarray(prices, dtype=float)
    now = t[-1]
    mask = t >= now - window_days
    tw, pw = t[mask], p[mask]
    if tw.size == 0:
        return DrawdownStats(window_days, 0.0, 0.0, now, now, 0, 0)
    dd = drawdown_series(pw)
    i_trough = int(np.argmin(dd))
    i_peak = int(np.argmax(pw[: i_trough + 1]))
    peak_val = pw[i_peak]
    recovery = None
    after = np.where(pw[i_trough:] >= peak_val)[0]
    if after.size and dd[i_trough] < 0:
        recovery = int(round(tw[i_trough + int(after[0])] - tw[i_trough]))
    elif dd[i_trough] >= 0:
        recovery = 0
    run_max_idx = int(np.argmax(pw))  # first index of the window max
    underwater = int(round(now - tw[run_max_idx])) if pw[-1] < pw[run_max_idx] else 0
    return DrawdownStats(
        window_days=window_days,
        max_drawdown=float(dd[i_trough]),
        current_drawdown=float(dd[-1]),
        peak_day=float(tw[i_peak]),
        trough_day=float(tw[i_trough]),
        recovery_days=recovery,
        underwater_days=underwater,
    )


def drawdown_profile(ts_days: np.ndarray, prices: np.ndarray, windows: tuple[int, ...] = (30, 60, 90)) -> dict[int, DrawdownStats]:
    return {w: window_stats(ts_days, prices, w) for w in windows}


def realized_vol_annualized(ts_days: np.ndarray, prices: np.ndarray, window_days: int = 90) -> float | None:
    t = np.asarray(ts_days, dtype=float)
    p = np.asarray(prices, dtype=float)
    mask = t >= t[-1] - window_days
    lp = np.log(p[mask])
    if lp.size < 5:
        return None
    dt = np.diff(t[mask])
    r = np.diff(lp) / np.sqrt(np.maximum(dt, 1e-9))  # per-sqrt-day scaling for irregular spacing
    return float(np.std(r, ddof=1) * np.sqrt(365.0))
