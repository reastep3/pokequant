"""SlabSpreadEnv — gym-style environment for a DQN agent trading grading/pairs spreads.

State (float32 vector):
    [arb_z, pairs_z, pop_momentum_z, liquidity/100, ev_gap_pct, dd_30d, position, unrealized_pnl_pct, days_held/90]
Actions: 0 = hold, 1 = buy raw & submit (open synthetic long spread), 2 = sell / close.
Reward: mark-to-market change in portfolio value net of transaction + grading costs, expressed
as a fraction of starting capital. Episodes are built from historical AnalyticsSnapshot rows
for one asset (see ``episodes_from_snapshots``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

STATE_DIM = 9
N_ACTIONS = 3


@dataclass
class EpisodeData:
    features: np.ndarray  # (T, 6): arb_z, pairs_z, pop_mom_z, liquidity, ev_gap, dd30
    prices: np.ndarray  # (T,) slab market price
    raw_prices: np.ndarray  # (T,) raw price
    grading_cost: float = 33.0
    sell_fee_pct: float = 0.1325


@dataclass
class SlabSpreadEnv:
    episode: EpisodeData
    capital: float = 10_000.0
    max_steps: int | None = None
    t: int = field(default=0, init=False)
    position: int = field(default=0, init=False)
    entry_cost: float = field(default=0.0, init=False)
    entry_t: int = field(default=0, init=False)
    cash: float = field(default=0.0, init=False)

    def reset(self) -> np.ndarray:
        self.t, self.position, self.entry_cost, self.entry_t, self.cash = 0, 0, 0.0, 0, self.capital
        return self._state()

    def _mark(self) -> float:
        if self.position == 0:
            return 0.0
        return self.episode.prices[self.t] * (1 - self.episode.sell_fee_pct)

    def _state(self) -> np.ndarray:
        f = self.episode.features[self.t]
        unreal = (self._mark() - self.entry_cost) / self.entry_cost if self.position else 0.0
        held = (self.t - self.entry_t) / 90.0 if self.position else 0.0
        return np.concatenate([f, [float(self.position), unreal, held]]).astype(np.float32)

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        value_before = self.cash + self._mark()
        if action == 1 and self.position == 0:
            self.entry_cost = self.episode.raw_prices[self.t] + self.episode.grading_cost
            if self.entry_cost <= self.cash:
                self.cash -= self.entry_cost
                self.position, self.entry_t = 1, self.t
        elif action == 2 and self.position == 1:
            self.cash += self._mark()
            self.position, self.entry_cost = 0, 0.0
        self.t += 1
        T = len(self.episode.prices) if self.max_steps is None else min(self.max_steps, len(self.episode.prices))
        done = self.t >= T - 1
        value_after = self.cash + self._mark()
        reward = (value_after - value_before) / self.capital
        return self._state(), float(reward), done, {"value": value_after}


def episodes_from_snapshots(rows: list[dict]) -> EpisodeData | None:
    """Build an episode from ordered AnalyticsSnapshot-like dicts (one asset, one grade)."""
    if len(rows) < 10:
        return None
    def col(k: str, default: float = 0.0) -> np.ndarray:
        return np.array([r.get(k) if r.get(k) is not None else default for r in rows], dtype=np.float32)
    feats = np.column_stack([col("arb_spread_z"), col("pairs_z"), col("pop_momentum_z"),
                             col("liquidity_score") / 100.0, col("ev_gap_pct"), col("dd_30d")])
    prices = col("market_price", np.nan)
    raw = col("raw_price", np.nan)
    if np.isnan(prices).any() or np.isnan(raw).any():
        return None
    return EpisodeData(features=feats, prices=prices, raw_prices=raw)
