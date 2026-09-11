"""Composite liquidity score in [0, 100] from trade frequency, recency, price dispersion, depth
and (when available) a bid/ask proxy. Each sub-score is a saturating map to [0, 1]."""

from __future__ import annotations

import numpy as np


def liquidity_score(
    *,
    trades_30d: float,
    days_since_last_trade: float,
    price_cv_30d: float | None,
    n_listings: float | None,
    spread_pct: float | None = None,
) -> dict:
    parts: dict[str, tuple[float, float]] = {}
    parts["frequency"] = (0.35, 1.0 - float(np.exp(-trades_30d / 10.0)))
    parts["recency"] = (0.15, float(np.exp(-max(days_since_last_trade, 0.0) / 14.0)))
    if price_cv_30d is not None:
        parts["tightness"] = (0.25, float(1.0 - np.clip(price_cv_30d / 0.5, 0.0, 1.0)))
    if n_listings is not None:
        parts["depth"] = (0.15, 1.0 - float(np.exp(-n_listings / 20.0)))
    if spread_pct is not None:
        parts["spread"] = (0.10, float(1.0 - np.clip(spread_pct / 0.20, 0.0, 1.0)))
    wsum = sum(w for w, _ in parts.values())
    score = 100.0 * sum(w * s for w, s in parts.values()) / wsum
    return {"score": float(score), "components": {k: v for k, (_, v) in parts.items()}}
