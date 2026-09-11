"""Synthetic oracle for demo mode / tests: geometric Brownian motion with regime bubbles,
grade-tiered slab prices, lognormal sale noise and a growing population."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np

from app.ingestion.base import AssetRef, PopSnapshot, PricePoint, SaleRecord

GRADE_MULT = {"PSA10": 1.0, "PSA9": 0.32, "PSA8": 0.18, "RAW": 0.14}


class SyntheticOracle:
    name = "synthetic"

    def __init__(self, days: int = 540, seed: int = 42):
        self.days = days
        self.seed = seed

    def _path(self, ref: AssetRef) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(self.seed + ref.asset_id * 7919)
        base = float(ref.external_ids.get("synthetic_base", 500.0))
        mu, sigma = rng.normal(0.0004, 0.0004), rng.uniform(0.012, 0.03)
        shocks = rng.normal(mu, sigma, self.days)
        # Occasional speculative regime: 60-120 day bubble that partially unwinds.
        if rng.random() < 0.6:
            start = rng.integers(self.days // 3, self.days - 90)
            length = int(rng.integers(60, 120))
            bump = np.zeros(self.days)
            bump[start : start + length] = np.sin(np.linspace(0, np.pi, min(length, self.days - start))) * rng.uniform(0.004, 0.01)
            shocks = shocks + bump
        lp = np.log(base) + np.cumsum(shocks)
        t = np.arange(self.days)
        return t, np.exp(lp)

    async def fetch_prices(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[PricePoint]:
        out: list[PricePoint] = []
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        for ref in refs:
            t, p10 = self._path(ref)
            rng = np.random.default_rng(self.seed + ref.asset_id)
            grades = ["SEALED"] if ref.external_ids.get("sealed") else list(GRADE_MULT)
            for gk in grades:
                mult = 1.0 if gk == "SEALED" else GRADE_MULT[gk]
                for i in range(self.days):
                    ts = now - timedelta(days=int(self.days - 1 - i))
                    if since and ts < since:
                        continue
                    px = float(p10[i] * mult * np.exp(rng.normal(0, 0.01)))
                    out.append(PricePoint(asset_id=ref.asset_id, grade_key=gk, ts=ts, price=round(px, 2),
                                          source=self.name, low=round(px * 0.96, 2), high=round(px * 1.05, 2),
                                          volume=int(rng.poisson(3 if gk != "PSA10" else 1.5)),
                                          n_listings=int(rng.poisson(18 * mult + 2))))
        return out

    async def fetch_sales(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[SaleRecord]:
        out: list[SaleRecord] = []
        now = datetime.now(UTC)
        for ref in refs:
            if ref.external_ids.get("sealed"):
                continue
            t, p10 = self._path(ref)
            rng = np.random.default_rng(self.seed + ref.asset_id * 13)
            liq = float(ref.external_ids.get("synthetic_liquidity", 0.4))
            for gk, mult in (("PSA10", 1.0), ("PSA9", 0.32)):
                n_sales = int(rng.poisson(self.days * liq * (0.5 if gk == "PSA10" else 1.0)))
                idx = np.sort(rng.integers(0, self.days, n_sales))
                for j, i in enumerate(idx):
                    px = p10[i] * mult * np.exp(rng.normal(0, 0.07))
                    if rng.random() < 0.02:
                        px *= rng.choice([0.3, 3.0])  # mis-listed lots
                    ts = now - timedelta(days=int(self.days - 1 - i), hours=int(rng.integers(0, 23)))
                    out.append(SaleRecord(asset_id=ref.asset_id, grade_key=gk, ts=ts, price=round(float(px), 2),
                                          source=self.name, external_id=f"syn-{ref.asset_id}-{gk}-{j}",
                                          cert_number=str(int(rng.integers(10_000_000, 99_999_999)))))
        return out

    async def fetch_pop(self, refs: Sequence[AssetRef]) -> list[PopSnapshot]:
        out: list[PopSnapshot] = []
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        for ref in refs:
            if ref.external_ids.get("sealed"):
                continue
            rng = np.random.default_rng(self.seed + ref.asset_id * 31)
            base_total = int(ref.external_ids.get("synthetic_pop", 5000))
            gem = float(rng.uniform(0.12, 0.45))
            weekly_rate = base_total / (52 * rng.uniform(4, 12))
            for w in range(int(self.days // 7), -1, -1):
                ts = now - timedelta(days=7 * w)
                total = int(base_total - weekly_rate * w * rng.uniform(0.8, 1.2))
                counts = {"10": int(total * gem), "9": int(total * 0.45), "8": int(total * 0.22)}
                counts["7"] = max(0, total - sum(counts.values()))
                out.append(PopSnapshot(asset_id=ref.asset_id, grader="PSA", ts=ts, grade_counts=counts,
                                       total=sum(counts.values()), source=self.name))
        return out

    async def aclose(self) -> None:
        return None
