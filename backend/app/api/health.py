from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import CacheDep, SessionDep, SettingsDep

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def ready(session: SessionDep, cache: CacheDep, settings: SettingsDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "db": "ok", "cache": cache.backend, "cache_ok": await cache.ping(),
            "demo_mode": settings.demo_mode, "env": settings.app_env}
