"""Screener ranking: Kelly-adjusted ROI blended with liquidity and valuation signals."""

from __future__ import annotations

import math


def kelly_adjusted_roi(kelly_fraction_scaled: float | None, expected_return: float | None) -> float | None:
    if kelly_fraction_scaled is None or expected_return is None:
        return None
    return kelly_fraction_scaled * expected_return


def screener_score(
    *,
    kelly_adj_roi: float | None,
    liquidity: float | None,
    ev_gap_pct: float | None = None,
    arb_z: float | None = None,
    bubble_premium_pct: float | None = None,
) -> float:
    """Composite ranking score.

    score = KAR * sqrt(liq/100) * (1 + 0.5 * clip(ev_gap, -0.5, 0.5)) * (1 + 0.1 * clip(arb_z, 0, 3))
            * (1 - 0.5 * clip(bubble, 0, 1))
    """
    kar = kelly_adj_roi or 0.0
    liq = max(0.0, min(100.0, liquidity or 0.0)) / 100.0
    gap = max(-0.5, min(0.5, ev_gap_pct or 0.0))
    z = max(0.0, min(3.0, arb_z if arb_z is not None and math.isfinite(arb_z) else 0.0))
    bub = max(0.0, min(1.0, bubble_premium_pct or 0.0))
    return kar * math.sqrt(liq) * (1 + 0.5 * gap) * (1 + 0.1 * z) * (1 - 0.5 * bub)
