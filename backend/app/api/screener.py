from fastapi import APIRouter, Query

from app.api.deps import CacheDep, SessionDep, SettingsDep
from app.services import screener_service

router = APIRouter()


@router.get("/screener")
async def get_screener(
    session: SessionDep, cache: CacheDep, settings: SettingsDep,
    sort: str = Query("screener_score"), desc: bool = True,
    asset_type: str | None = Query(None, pattern="^(card|sealed)$"),
    language: str | None = Query(None, pattern="^(en|ja)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    return await screener_service.screener(session, cache, sort=sort, desc=desc, asset_type=asset_type,
                                           language=language, limit=limit, ttl=settings.cache_ttl_s)
