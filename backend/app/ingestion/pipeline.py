"""Ingestion orchestration: oracle fan-out -> normalization -> idempotent upserts -> audit row."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db.session import upsert
from app.ingestion.base import AssetRef, PopSnapshot, PricePoint, SaleRecord
from app.ingestion.parsebot_psa import ParseBotPSAClient
from app.ingestion.pokemonpricetracker import PokemonPriceTrackerClient
from app.ingestion.synthetic import SyntheticOracle
from app.models import Asset, IngestionRun, PopReport, PriceTick, Sale

log = logging.getLogger(__name__)


def build_oracles(settings: Settings | None = None) -> dict[str, object]:
    s = settings or get_settings()
    if s.demo_mode:
        syn = SyntheticOracle()
        return {"prices": [syn], "sales": [syn], "pop": [syn]}
    oracles: dict[str, list] = {"prices": [], "sales": [], "pop": []}
    if s.ppt_api_key:
        ppt = PokemonPriceTrackerClient(s.ppt_base_url, s.ppt_api_key, rps=s.oracle_rps, timeout=s.oracle_timeout_s)
        oracles["prices"].append(ppt)
    if s.parsebot_api_key:
        pb = ParseBotPSAClient(s.parsebot_base_url, s.parsebot_api_key, rps=s.oracle_rps, timeout=s.oracle_timeout_s)
        oracles["sales"].append(pb)
        oracles["pop"].append(pb)
    return oracles


async def load_refs(session: AsyncSession, only_cards: bool = False) -> list[AssetRef]:
    q = select(Asset).options(selectinload(Asset.set)).where(Asset.is_active.is_(True))
    rows = (await session.execute(q)).scalars().all()
    refs = []
    for a in rows:
        if only_cards and a.asset_type.value != "card":
            continue
        ext = dict(a.external_ids or {})
        if a.asset_type.value == "sealed":
            ext["sealed"] = True
        refs.append(AssetRef(asset_id=a.id, ticker=a.ticker, external_ids=ext, name=a.name,
                             set_code=a.set.code if a.set else None, number=a.number))
    return refs


class IngestionRunCtx:
    def __init__(self, session: AsyncSession, job: str):
        self.session, self.job, self.run = session, job, None

    async def __aenter__(self) -> IngestionRunCtx:
        self.run = IngestionRun(job=self.job, started_at=datetime.now(UTC))
        self.session.add(self.run)
        await self.session.flush()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        assert self.run is not None
        self.run.finished_at = datetime.now(UTC)
        self.run.status = "error" if exc else "ok"
        if exc:
            self.run.error = f"{exc_type.__name__}: {exc}"[:2000]
            log.exception("job %s failed", self.job)
        await self.session.commit()
        return True  # swallow: the audit row carries the failure; scheduler keeps running


def _tick_rows(points: list[PricePoint]) -> list[dict]:
    return [dict(asset_id=p.asset_id, grade_key=p.grade_key, source=p.source, ts=p.ts, price=p.price,
                 currency=p.currency, low=p.low, high=p.high, volume=p.volume, n_listings=p.n_listings,
                 bid=p.bid, ask=p.ask, meta=p.meta) for p in points]


def aggregate_sales(sales: list[SaleRecord]) -> list[PricePoint]:
    """Daily bars (median price, min/max, count) from lots -> source='agg'."""
    buckets: dict[tuple[int, str, datetime], list[float]] = defaultdict(list)
    for s in sales:
        day = s.ts.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets[(s.asset_id, s.grade_key, day)].append(s.price)
    out = []
    for (aid, gk, day), prices in buckets.items():
        prices.sort()
        mid = prices[len(prices) // 2] if len(prices) % 2 else (prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / 2
        out.append(PricePoint(asset_id=aid, grade_key=gk, ts=day, price=mid, source="agg",
                              low=prices[0], high=prices[-1], volume=len(prices)))
    return out


async def run_price_ingest(session: AsyncSession, settings: Settings | None = None, since_days: int = 3) -> int:
    s = settings or get_settings()
    oracles = build_oracles(s)
    since = None if s.demo_mode else datetime.now(UTC) - timedelta(days=since_days)
    written = 0
    async with IngestionRunCtx(session, "price_ingest") as ctx:
        refs = await load_refs(session)
        for oracle in oracles["prices"]:
            points = await oracle.fetch_prices(refs, since=since)
            written += await upsert(session, PriceTick, _tick_rows(points), ["asset_id", "grade_key", "source", "ts"],
                                    ["price", "low", "high", "volume", "n_listings", "bid", "ask", "meta"])
        for oracle in oracles["sales"]:
            sales = await oracle.fetch_sales(refs, since=since)
            rows = [dict(asset_id=x.asset_id, grade_key=x.grade_key, ts=x.ts, price=x.price, currency=x.currency,
                         source=x.source, external_id=x.external_id, cert_number=x.cert_number, meta=x.meta) for x in sales]
            written += await upsert(session, Sale, rows, ["source", "external_id"])
            written += await upsert(session, PriceTick, _tick_rows(aggregate_sales(sales)),
                                    ["asset_id", "grade_key", "source", "ts"], ["price", "low", "high", "volume"])
        for o in {*oracles["prices"], *oracles["sales"]}:
            await o.aclose()
        ctx.run.rows_written = written
    return written


async def run_pop_ingest(session: AsyncSession, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    oracles = build_oracles(s)
    written = 0
    async with IngestionRunCtx(session, "pop_ingest") as ctx:
        refs = await load_refs(session, only_cards=True)
        for oracle in oracles["pop"]:
            snaps: list[PopSnapshot] = await oracle.fetch_pop(refs)
            rows = []
            for p in snaps:
                tens = p.grade_counts.get("10", 0)
                rows.append(dict(asset_id=p.asset_id, grader=p.grader, ts=p.ts, grade_counts=p.grade_counts,
                                 total=p.total, gem_rate=(tens / p.total) if p.total else None, source=p.source, meta=p.meta))
            written += await upsert(session, PopReport, rows, ["asset_id", "grader", "ts"],
                                    ["grade_counts", "total", "gem_rate", "meta"])
            await oracle.aclose()
        ctx.run.rows_written = written
    return written
