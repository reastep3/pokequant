"""Operator endpoints (X-API-Key): trigger jobs on demand, inspect ingestion audit."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CacheDep, SessionDep, SettingsDep, require_api_key
from app.api.stream import CHANNEL
from app.ingestion.pipeline import run_pop_ingest, run_price_ingest
from app.models import IngestionRun
from app.services import screener_service
from app.services.analytics_service import run_analytics

router = APIRouter(prefix="/admin", dependencies=[Depends(require_api_key)])
JOBS = {"price_ingest": run_price_ingest, "pop_ingest": run_pop_ingest, "analytics": run_analytics}


@router.post("/jobs/{job}")
async def run_job(job: str, session: SessionDep, cache: CacheDep, settings: SettingsDep) -> dict:
    fn = JOBS.get(job)
    if not fn:
        raise HTTPException(404, f"unknown job; choose from {sorted(JOBS)}")
    n = await fn(session, settings)
    if job == "analytics":
        await screener_service.invalidate(cache)
        await cache.publish(CHANNEL, {"job": job, "rows": n, "ts": datetime.now(UTC).isoformat()})
    return {"job": job, "rows": n}


@router.get("/runs")
async def runs(session: SessionDep, limit: int = Query(50, le=500)) -> dict:
    rows = (await session.execute(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit))).scalars().all()
    return {"rows": [{"id": r.id, "job": r.job, "status": r.status, "rows_written": r.rows_written,
                      "started_at": r.started_at.isoformat() if r.started_at else None,
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None, "error": r.error} for r in rows]}
