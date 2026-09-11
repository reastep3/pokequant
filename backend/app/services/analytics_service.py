"""Loads time-series rows per asset, runs the QuantEngine, persists AnalyticsSnapshot + Signals."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db.session import upsert
from app.ingestion.pipeline import IngestionRunCtx
from app.models import (
    AnalyticsSnapshot,
    Asset,
    AssetType,
    PopReport,
    PriceTick,
    PrintEquivalence,
    PullRate,
    Sale,
    SealedProduct,
    Signal,
)
from app.models.analytics import SignalKind, SignalStatus
from app.quant import QuantEngine, SealedInputs, SlabInputs
from app.quant.kelly import grade_distribution_from_pop
from app.quant.quant_engine import EngineParams
from app.quant.sealed_ev import PullSlot

log = logging.getLogger(__name__)
PRIMARY_GRADE = "PSA10"
SNAPSHOT_COLUMNS = {c.name for c in AnalyticsSnapshot.__table__.columns}


def engine_from_settings(s: Settings) -> QuantEngine:
    return QuantEngine(EngineParams(grading_fee=s.grading_fee_usd, shipping=s.grading_shipping_usd,
                                    sell_fee_pct=s.marketplace_fee_pct, rf_annual=s.risk_free_rate,
                                    kelly_multiplier=s.kelly_multiplier, kelly_cap=s.kelly_fraction_cap,
                                    z_threshold=s.signal_z_threshold))


async def _series(session: AsyncSession, asset_id: int, grade_key: str, days: int = 730) -> tuple[np.ndarray, np.ndarray, list[PriceTick]]:
    """Daily price series for (asset, grade): prefer the first source seen per day ('agg' > oracle)."""
    since = datetime.now(UTC) - timedelta(days=days)
    q = (select(PriceTick).where(PriceTick.asset_id == asset_id, PriceTick.grade_key == grade_key, PriceTick.ts >= since)
         .order_by(PriceTick.ts.asc()))
    ticks = (await session.execute(q)).scalars().all()
    by_day: dict[datetime, PriceTick] = {}
    for t in ticks:
        d = t.ts.replace(hour=0, minute=0, second=0, microsecond=0)
        if d not in by_day or (t.source == "agg" and by_day[d].source != "agg"):
            by_day[d] = t
    if not by_day:
        return np.array([]), np.array([]), []
    days_sorted = sorted(by_day)
    t0 = days_sorted[0]
    ts = np.array([(d - t0).total_seconds() / 86400 for d in days_sorted])
    px = np.array([float(by_day[d].price) for d in days_sorted])
    return ts, px, [by_day[d] for d in days_sorted]


async def _latest_price(session: AsyncSession, asset_id: int, grade_key: str) -> float | None:
    q = (select(PriceTick.price).where(PriceTick.asset_id == asset_id, PriceTick.grade_key == grade_key)
         .order_by(PriceTick.ts.desc()).limit(1))
    v = (await session.execute(q)).scalar()
    return float(v) if v is not None else None


async def _pop(session: AsyncSession, asset_id: int) -> tuple[dict, list[tuple[float, int]]]:
    q = select(PopReport).where(PopReport.asset_id == asset_id, PopReport.grader == "PSA").order_by(PopReport.ts.asc())
    rows = (await session.execute(q)).scalars().all()
    if not rows:
        return {}, []
    t0 = rows[0].ts
    hist = [((r.ts - t0).total_seconds() / 86400, r.total) for r in rows]
    return dict(rows[-1].grade_counts), hist


async def _pair_counterpart(session: AsyncSession, asset: Asset) -> int | None:
    if asset.language.value == "ja":
        q = select(PrintEquivalence.en_asset_id).where(PrintEquivalence.ja_asset_id == asset.id)
    else:
        q = select(PrintEquivalence.ja_asset_id).where(PrintEquivalence.en_asset_id == asset.id)
    v = (await session.execute(q)).scalar()
    if v:
        return int(v)
    q2 = select(Asset.id).where(Asset.canonical_key == asset.canonical_key, Asset.id != asset.id,
                                Asset.asset_type == AssetType.CARD).limit(1)
    v2 = (await session.execute(q2)).scalar()
    return int(v2) if v2 else None


def _align(ts_a: np.ndarray, px_a: np.ndarray, t0_a: datetime, ts_b: np.ndarray, px_b: np.ndarray, t0_b: datetime, n: int = 240):
    """Align two daily series on calendar day; return (log_a, log_b) for the trailing n common days."""
    da = {int(round(x + (t0_a - datetime(1970, 1, 1, tzinfo=UTC)).days)): p for x, p in zip(ts_a, px_a, strict=True)}
    db = {int(round(x + (t0_b - datetime(1970, 1, 1, tzinfo=UTC)).days)): p for x, p in zip(ts_b, px_b, strict=True)}
    common = sorted(set(da) & set(db))[-n:]
    if len(common) < 30:
        return None
    return np.log([da[d] for d in common]), np.log([db[d] for d in common])


async def _monthly_cs_inputs(session: AsyncSession, asset: Asset, ticks: list[PriceTick], pop_hist: list[tuple[float, int]]):
    """Month-end log price, monthly turnover (sum of volume) and interpolated population."""
    if len(ticks) < 120 or len(pop_hist) < 4:
        return None
    months: dict[tuple[int, int], list[PriceTick]] = {}
    for t in ticks:
        months.setdefault((t.ts.year, t.ts.month), []).append(t)
    # Partial months (first/current) would understate turnover and fake a valuation jump.
    keys = [k for k in sorted(months) if len({t.ts.date() for t in months[k]}) >= 10]
    if len(keys) < 16:
        return None
    lp = np.array([np.log(float(months[k][-1].price)) for k in keys])
    # Turnover as lots per observed day, then a trailing 3-month mean: monthly lot counts are
    # Poisson-noisy while the fundamental *flow* is not.
    raw_turnover = np.array([sum((t.volume or 0) for t in months[k]) / len({t.ts.date() for t in months[k]}) for k in keys])
    turnover = np.array([raw_turnover[max(0, i - 2) : i + 1].mean() for i in range(raw_turnover.size)]) * 30.0
    t0 = ticks[0].ts
    month_days = np.array([(months[k][-1].ts - t0).total_seconds() / 86400 for k in keys])
    ph = np.array(pop_hist)
    pop_series = np.interp(month_days, ph[:, 0], ph[:, 1])
    release = asset.set.release_date if asset.set and asset.set.release_date else None
    age = None
    if release:
        age = np.array([max(0.0, (months[k][-1].ts.date() - release).days / 365.25) for k in keys])
    return lp, turnover, pop_series, age


async def _liquidity_inputs(session: AsyncSession, asset_id: int, grade_key: str, latest_tick: PriceTick | None):
    since = datetime.now(UTC) - timedelta(days=30)
    q = select(Sale.price, Sale.ts).where(Sale.asset_id == asset_id, Sale.grade_key == grade_key, Sale.ts >= since)
    rows = (await session.execute(q)).all()
    prices = np.array([float(r[0]) for r in rows])
    last_q = select(func.max(Sale.ts)).where(Sale.asset_id == asset_id, Sale.grade_key == grade_key)
    last = (await session.execute(last_q)).scalar()
    if last is None and latest_tick is not None:
        last = latest_tick.ts
    days_since = (datetime.now(UTC) - last).total_seconds() / 86400 if last else 999.0
    cv = float(prices.std(ddof=1) / prices.mean()) if prices.size > 2 and prices.mean() > 0 else None
    trades = float(prices.size) if prices.size else float(latest_tick.volume or 0) if latest_tick else 0.0
    n_listings = float(latest_tick.n_listings) if latest_tick and latest_tick.n_listings is not None else None
    return trades, days_since, cv, n_listings


async def _arb_spread_history(raw_ts: np.ndarray, raw_px: np.ndarray, raw_t0: datetime, grade_series: dict[str, tuple],
                              probs: dict[str, float], fee: float, ship: float, sell_fee: float) -> np.ndarray | None:
    """Daily EGV - cost using constant grade probabilities and each grade's daily price."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    raw_map = {int(round(x + (raw_t0 - epoch).days)): p for x, p in zip(raw_ts, raw_px, strict=True)}
    grade_maps = {}
    for g, (ts, px, t0) in grade_series.items():
        grade_maps[g] = {int(round(x + (t0 - epoch).days)): p for x, p in zip(ts, px, strict=True)}
    days = sorted(set(raw_map) & set.intersection(*[set(m) for m in grade_maps.values()])) if grade_maps else []
    if len(days) < 30:
        return None
    out = []
    for d in days[-365:]:
        egv = sum(p * grade_maps[g][d] * (1 - sell_fee) for g, p in probs.items() if g in grade_maps) \
            + sum(p * raw_map[d] * (1 - sell_fee) for g, p in probs.items() if g not in grade_maps)
        out.append(egv - (raw_map[d] + fee + ship))
    return np.array(out)


async def analyze_card(session: AsyncSession, asset: Asset, engine: QuantEngine, s: Settings) -> dict | None:
    ts, px, ticks = await _series(session, asset.id, PRIMARY_GRADE)
    if ts.size < 10:
        return None
    t0 = ticks[0].ts
    since_sales = datetime.now(UTC) - timedelta(days=730)
    sales = (await session.execute(select(Sale.ts, Sale.price).where(Sale.asset_id == asset.id, Sale.grade_key == PRIMARY_GRADE,
                                                                     Sale.ts >= since_sales).order_by(Sale.ts))).all()
    sales_ts = np.array([(r[0] - t0).total_seconds() / 86400 for r in sales]) if sales else None
    sales_px = np.array([float(r[1]) for r in sales]) if sales else None

    raw_price = await _latest_price(session, asset.id, "RAW")
    slab_prices = {}
    grade_series = {}
    for gk, label in (("PSA10", "10"), ("PSA9", "9"), ("PSA8", "8")):
        p = await _latest_price(session, asset.id, gk)
        if p is not None:
            slab_prices[label] = p
        gts, gpx, gticks = await _series(session, asset.id, gk, days=400)
        if gts.size:
            grade_series[label] = (gts, gpx, gticks[0].ts)
    pop_counts, pop_hist = await _pop(session, asset.id)

    arb_hist = None
    if raw_price and pop_counts:
        try:
            probs = grade_distribution_from_pop(pop_counts, condition_tilt=engine.p.condition_tilt)
            rts, rpx, rticks = await _series(session, asset.id, "RAW", days=400)
            if rts.size:
                arb_hist = await _arb_spread_history(rts, rpx, rticks[0].ts, grade_series, probs,
                                                     s.grading_fee_usd, s.grading_shipping_usd, s.marketplace_fee_pct)
        except ValueError:
            pass

    pair = None
    cp_id = await _pair_counterpart(session, asset)
    if cp_id:
        cts, cpx, cticks = await _series(session, cp_id, PRIMARY_GRADE)
        if cts.size:
            pair = _align(ts, px, t0, cts, cpx, cticks[0].ts)

    cs = await _monthly_cs_inputs(session, asset, ticks, pop_hist)
    trades, days_since, cv, n_listings = await _liquidity_inputs(session, asset.id, PRIMARY_GRADE, ticks[-1])
    latest = ticks[-1]
    spread_pct = None
    if latest.bid and latest.ask and latest.ask > 0:
        spread_pct = float((latest.ask - latest.bid) / latest.ask)

    x = SlabInputs(
        ticker=asset.ticker, grade_key=PRIMARY_GRADE, ts_days=ts, prices=px, sales_ts_days=sales_ts, sales_prices=sales_px,
        market_price=float(px[-1]), raw_price=raw_price, slab_prices=slab_prices, pop_counts=pop_counts, pop_history=pop_hist,
        arb_spread_history=arb_hist, pair_log_prices=pair,
        cs_log_prices=cs[0] if cs else None, turnover_series=cs[1] if cs else None, pop_series=cs[2] if cs else None,
        age_years=cs[3] if cs else None, trades_30d=trades, days_since_last_trade=days_since, price_cv_30d=cv,
        n_listings=n_listings, spread_pct=spread_pct,
    )
    out = engine.analyze_slab(x)
    out["payload"]["raw_price"] = raw_price
    out["payload"]["slab_prices"] = slab_prices
    out["payload"]["pop_counts"] = pop_counts
    out["payload"]["series"] = {"ts_days": ts.tolist(), "prices": px.tolist(), "t0": t0.isoformat()}
    return out


async def analyze_sealed(session: AsyncSession, asset: Asset, engine: QuantEngine, s: Settings) -> dict | None:
    sp = (await session.execute(select(SealedProduct).where(SealedProduct.asset_id == asset.id))).scalar()
    if sp is None:
        return None
    ts, px, ticks = await _series(session, asset.id, "SEALED")
    price = float(px[-1]) if px.size else float(sp.msrp or 0)
    if price <= 0:
        return None
    rates = (await session.execute(select(PullRate).where(PullRate.set_id == sp.set_id))).scalars().all()
    slots: list[PullSlot] = []
    for r in rates:
        if r.asset_id:
            v = await _latest_price(session, r.asset_id, "RAW")
            if v:
                slots.append(PullSlot(r.slot_name, float(r.prob_per_pack), np.array([v])))
        elif r.rarity_tier:
            q = select(Asset.id).where(Asset.set_id == sp.set_id, Asset.rarity == r.rarity_tier, Asset.asset_type == AssetType.CARD)
            ids = (await session.execute(q)).scalars().all()
            vals = [await _latest_price(session, i, "RAW") for i in ids]
            vals = [v for v in vals if v]
            if not vals and r.meta and r.meta.get("avg_value"):
                vals = [float(r.meta["avg_value"])]
            if vals:
                slots.append(PullSlot(r.slot_name, float(r.prob_per_pack), np.array(vals, dtype=float)))
    if not slots:
        return None
    trades, days_since, cv, n_listings = await _liquidity_inputs(session, asset.id, "SEALED", ticks[-1] if ticks else None)
    x = SealedInputs(ticker=asset.ticker, price=price, n_packs=sp.packs_per_unit, slots=slots,
                     bulk_value_per_pack=float(sp.bulk_value_per_pack), ts_days=ts if ts.size else None,
                     prices=px if px.size else None, trades_30d=trades, days_since_last_trade=days_since,
                     price_cv_30d=cv, n_listings=n_listings)
    out = engine.analyze_sealed(x)
    if ts.size:
        out["payload"]["series"] = {"ts_days": ts.tolist(), "prices": px.tolist(), "t0": ticks[0].ts.isoformat()}
    out["payload"]["product"] = {"type": sp.product_type, "packs": sp.packs_per_unit, "msrp": float(sp.msrp) if sp.msrp else None}
    return out


def _snapshot_row(asset_id: int, out: dict, ts: datetime) -> dict:
    row = {k: out.get(k) for k in SNAPSHOT_COLUMNS}
    for k, v in list(row.items()):
        if isinstance(v, float) and not np.isfinite(v):
            row[k] = None
        elif isinstance(v, np.floating | np.integer):
            row[k] = v.item()
    row.update(asset_id=asset_id, ts=ts, grade_key=out["grade_key"])
    return row


async def run_analytics(session: AsyncSession, settings: Settings | None = None, asset_ids: list[int] | None = None) -> int:
    s = settings or get_settings()
    engine = engine_from_settings(s)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    n = 0
    async with IngestionRunCtx(session, "analytics") as ctx:
        q = select(Asset).options(selectinload(Asset.set)).where(Asset.is_active.is_(True))
        if asset_ids:
            q = q.where(Asset.id.in_(asset_ids))
        assets = (await session.execute(q)).scalars().all()
        rows = []
        for a in assets:
            try:
                out = await (analyze_card if a.asset_type == AssetType.CARD else analyze_sealed)(session, a, engine, s)
            except Exception:  # noqa: BLE001
                log.exception("analytics failed for %s", a.ticker)
                continue
            if out:
                rows.append(_snapshot_row(a.id, out, now))
        n = await upsert(session, AnalyticsSnapshot, rows, ["asset_id", "grade_key", "ts"],
                         [c for c in SNAPSHOT_COLUMNS if c not in {"asset_id", "grade_key", "ts"}], commit_each=True)
        ctx.rows_written = n
        await generate_signals(session, rows, s)
    return n


async def generate_signals(session: AsyncSession, rows: list[dict], s: Settings) -> int:
    """Re-derive signals from fresh snapshots: update persisting ones, expire the rest, create new."""
    now = datetime.now(UTC)
    ttl = timedelta(days=3)
    triggered: dict[tuple[int, SignalKind], tuple[str, float, str, dict]] = {}
    for r in rows:
        for kind, direction, strength, headline in _candidates(r, s):
            triggered[(r["asset_id"], kind)] = (direction, strength, headline, r)
    asset_ids = list({r["asset_id"] for r in rows})
    if asset_ids:
        active = (await session.execute(select(Signal).where(Signal.asset_id.in_(asset_ids),
                                                             Signal.status == SignalStatus.ACTIVE))).scalars().all()
        for sig in active:
            key = (sig.asset_id, sig.kind)
            if key in triggered:
                direction, strength, headline, r = triggered.pop(key)
                sig.direction, sig.strength, sig.headline = direction, float(strength), headline
                sig.expires_at, sig.payload = now + ttl, _sig_payload(r)
            else:
                sig.status = SignalStatus.EXPIRED
    created = 0
    for (asset_id, kind), (direction, strength, headline, r) in triggered.items():
        session.add(Signal(asset_id=asset_id, grade_key=r["grade_key"], kind=kind, direction=direction,
                           strength=float(strength), headline=headline, expires_at=now + ttl, payload=_sig_payload(r)))
        created += 1
    await session.execute(Signal.__table__.update().where(and_(Signal.status == SignalStatus.ACTIVE, Signal.expires_at < now))
                          .values(status=SignalStatus.EXPIRED))
    await session.commit()
    return created


def _sig_payload(r: dict) -> dict:
    return {k: r.get(k) for k in ("market_price", "fair_value", "ev_gap_pct", "kelly_fraction_scaled", "liquidity_score")}


def _candidates(r: dict, s: Settings) -> list[tuple[SignalKind, str, float, str]]:
    out: list[tuple[SignalKind, str, float, str]] = []
    z = r.get("arb_spread_z")
    if z is not None and abs(z) >= s.signal_z_threshold:
        out.append((SignalKind.GRADING_ARB, "long" if z > 0 else "short", z,
                    f"Grading spread {z:+.1f}σ vs 90d: EGV − cost = ${r.get('arb_spread') or 0:,.0f}"))
    pz, hl = r.get("pairs_z"), r.get("pairs_half_life_days")
    if pz is not None and abs(pz) >= s.signal_z_threshold and hl and hl < 120:
        out.append((SignalKind.JP_EN_PAIRS, "short" if pz > 0 else "long", pz, f"JP/EN spread {pz:+.1f}σ, half-life {hl:.0f}d"))
    ev = r.get("sealed_ev_ratio")
    if ev is not None and (ev >= 1.15 or ev <= 0.6):
        out.append((SignalKind.SEALED_EV, "long" if ev >= 1.15 else "short", (ev - 1) * 10,
                    f"Sealed EV/price {ev:.2f}, P(profit) {r.get('sealed_p_profit') or 0:.0%}"))
    bub = r.get("cs_bubble_premium_pct")
    if bub is not None and bub >= 0.35:
        out.append((SignalKind.BUBBLE, "short", bub * 5, f"Speculative premium {bub:.0%} above Campbell–Shiller fundamental"))
    dd90, rec, gap = r.get("dd_90d"), r.get("recovery_90d_days"), r.get("ev_gap_pct")
    if dd90 is not None and dd90 <= -0.25 and rec is None and gap is not None and gap > 0.15:
        out.append((SignalKind.DRAWDOWN_RECOVERY, "long", gap * 10,
                    f"{dd90:.0%} 90d drawdown, unrecovered, {gap:+.0%} below kernel fair value"))
    return out
