"""Screener + asset detail reads: latest snapshot per (asset, grade) joined to reference data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import Cache
from app.models import AnalyticsSnapshot, Asset, CardSet, Signal
from app.models.analytics import SignalStatus

SORTABLE = {
    "screener_score", "kelly_adj_roi", "kelly_fraction_scaled", "liquidity_score", "ev_gap_pct", "dd_30d", "dd_60d",
    "dd_90d", "arb_spread_z", "pairs_z", "cs_bubble_premium_pct", "market_price", "vol_90d_ann", "ret_30d",
    "sealed_ev_ratio", "pop_velocity_30d",
}
SCREENER_FIELDS = [
    "market_price", "fair_value", "ev_gap_pct", "ret_30d", "vol_90d_ann", "dd_30d", "dd_60d", "dd_90d",
    "recovery_30d_days", "recovery_60d_days", "recovery_90d_days", "kelly_fraction", "kelly_fraction_scaled",
    "expected_return", "kelly_adj_roi", "liquidity_score", "screener_score", "arb_spread", "arb_spread_z", "pairs_z",
    "pairs_half_life_days", "cs_bubble_premium_pct", "pop_total", "pop_gem_rate", "pop_velocity_30d", "pop_momentum_z",
    "sealed_ev", "sealed_ev_ratio", "sealed_p_profit", "underwater_days",
]


GEN_KEY = "screener:gen"


async def invalidate(cache: Cache) -> int:
    """Bump the screener cache generation (called after every analytics run)."""
    gen = int(await cache.get_json(GEN_KEY) or 0) + 1
    await cache.set_json(GEN_KEY, gen, ttl=86_400 * 30)
    return gen


def _latest_subq():
    return (select(AnalyticsSnapshot.asset_id, AnalyticsSnapshot.grade_key, func.max(AnalyticsSnapshot.ts).label("ts"))
            .group_by(AnalyticsSnapshot.asset_id, AnalyticsSnapshot.grade_key).subquery())


def _asset_dict(a: Asset, cs: CardSet | None) -> dict:
    return {"id": a.id, "ticker": a.ticker, "name": a.name, "asset_type": a.asset_type.value, "language": a.language.value,
            "set_code": cs.code if cs else None, "set_name": cs.name if cs else None, "number": a.number,
            "rarity": a.rarity, "canonical_key": a.canonical_key, "image_url": a.image_url}


async def screener(session: AsyncSession, cache: Cache, *, sort: str = "screener_score", desc: bool = True,
                   asset_type: str | None = None, language: str | None = None, limit: int = 200, ttl: int = 30) -> dict:
    sort = sort if sort in SORTABLE else "screener_score"
    gen = await cache.get_json(GEN_KEY) or 0
    key = f"screener:{gen}:{sort}:{desc}:{asset_type}:{language}:{limit}"
    cached = await cache.get_json(key)
    if cached:
        return cached
    latest = _latest_subq()
    q = (select(AnalyticsSnapshot, Asset, CardSet)
         .join(latest, (AnalyticsSnapshot.asset_id == latest.c.asset_id) & (AnalyticsSnapshot.grade_key == latest.c.grade_key)
               & (AnalyticsSnapshot.ts == latest.c.ts))
         .join(Asset, Asset.id == AnalyticsSnapshot.asset_id)
         .outerjoin(CardSet, CardSet.id == Asset.set_id)
         .where(Asset.is_active.is_(True)))
    if asset_type:
        q = q.where(Asset.asset_type == asset_type)
    if language:
        q = q.where(Asset.language == language)
    col = getattr(AnalyticsSnapshot, sort)
    q = q.order_by(col.desc().nulls_last() if desc else col.asc().nulls_last()).limit(limit)
    rows = (await session.execute(q)).all()
    out = {"as_of": datetime.now(UTC).isoformat(), "sort": sort, "desc": desc, "count": len(rows), "rows": []}
    for snap, asset, cs in rows:
        d = _asset_dict(asset, cs)
        d["grade_key"] = snap.grade_key
        d["snapshot_ts"] = snap.ts.isoformat()
        for f in SCREENER_FIELDS:
            d[f] = getattr(snap, f)
        out["rows"].append(d)
    await cache.set_json(key, out, ttl)
    return out


async def asset_detail(session: AsyncSession, asset_id: int) -> dict | None:
    a = (await session.execute(select(Asset, CardSet).outerjoin(CardSet, CardSet.id == Asset.set_id).where(Asset.id == asset_id))).first()
    if not a:
        return None
    asset, cs = a
    snap = (await session.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.asset_id == asset_id)
                                  .order_by(AnalyticsSnapshot.ts.desc()).limit(1))).scalar()
    sigs = (await session.execute(select(Signal).where(Signal.asset_id == asset_id, Signal.status == SignalStatus.ACTIVE)
                                  .order_by(Signal.created_at.desc()).limit(10))).scalars().all()
    d = {"asset": _asset_dict(asset, cs), "analytics": None, "signals": [_signal_dict(s, asset, cs) for s in sigs]}
    if snap:
        d["analytics"] = {f: getattr(snap, f) for f in SCREENER_FIELDS}
        d["analytics"].update(grade_key=snap.grade_key, ts=snap.ts.isoformat(), fair_value_lo=snap.fair_value_lo,
                              fair_value_hi=snap.fair_value_hi, cs_fundamental_value=snap.cs_fundamental_value,
                              cs_bubble_share=snap.cs_bubble_share, payload=snap.payload)
    return d


def _signal_dict(s: Signal, a: Asset | None = None, cs: CardSet | None = None) -> dict:
    return {"id": s.id, "asset_id": s.asset_id, "ticker": a.ticker if a else None, "name": a.name if a else None,
            "grade_key": s.grade_key, "kind": s.kind.value, "direction": s.direction, "strength": s.strength,
            "headline": s.headline, "payload": s.payload, "status": s.status.value,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None}


async def signals(session: AsyncSession, *, status: str = "active", limit: int = 100) -> list[dict]:
    q = (select(Signal, Asset, CardSet).join(Asset, Asset.id == Signal.asset_id).outerjoin(CardSet, CardSet.id == Asset.set_id)
         .where(Signal.status == status).order_by(func.abs(Signal.strength).desc()).limit(limit))
    return [_signal_dict(s, a, cs) for s, a, cs in (await session.execute(q)).all()]


async def sealed_matrix(session: AsyncSession) -> list[dict]:
    latest = _latest_subq()
    q = (select(AnalyticsSnapshot, Asset, CardSet)
         .join(latest, (AnalyticsSnapshot.asset_id == latest.c.asset_id) & (AnalyticsSnapshot.grade_key == latest.c.grade_key)
               & (AnalyticsSnapshot.ts == latest.c.ts))
         .join(Asset, Asset.id == AnalyticsSnapshot.asset_id).outerjoin(CardSet, CardSet.id == Asset.set_id)
         .where(AnalyticsSnapshot.grade_key == "SEALED").order_by(AnalyticsSnapshot.ev_gap_pct.desc().nulls_last()))
    out = []
    for snap, a, cs in (await session.execute(q)).all():
        d = _asset_dict(a, cs)
        d.update(market_price=snap.market_price, sealed_ev=snap.sealed_ev, sealed_ev_ratio=snap.sealed_ev_ratio,
                 sealed_p_profit=snap.sealed_p_profit, ev_gap_pct=snap.ev_gap_pct, dd_90d=snap.dd_90d,
                 liquidity_score=snap.liquidity_score, kelly_adj_roi=snap.kelly_adj_roi,
                 ev=(snap.payload or {}).get("sealed_ev"), product=(snap.payload or {}).get("product"))
        out.append(d)
    return out


async def price_history(session: AsyncSession, asset_id: int, grade_key: str, days: int = 365) -> list[dict]:
    from app.models import PriceTick
    since = datetime.now(UTC) - timedelta(days=days)
    q = (select(PriceTick).where(PriceTick.asset_id == asset_id, PriceTick.grade_key == grade_key, PriceTick.ts >= since)
         .order_by(PriceTick.ts.asc()))
    seen: dict[str, dict] = {}
    for t in (await session.execute(q)).scalars():
        day = t.ts.date().isoformat()
        if day not in seen or t.source == "agg":
            seen[day] = {"time": day, "value": float(t.price), "low": float(t.low) if t.low else None,
                         "high": float(t.high) if t.high else None, "volume": t.volume, "source": t.source}
    return list(seen.values())
