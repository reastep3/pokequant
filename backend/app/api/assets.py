from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SessionDep
from app.services import screener_service

router = APIRouter()


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: int, session: SessionDep) -> dict:
    d = await screener_service.asset_detail(session, asset_id)
    if not d:
        raise HTTPException(404, "asset not found")
    return d


@router.get("/assets/{asset_id}/prices")
async def get_prices(asset_id: int, session: SessionDep, grade: str = Query("PSA10"), days: int = Query(365, ge=7, le=3650)) -> dict:
    return {"asset_id": asset_id, "grade_key": grade, "bars": await screener_service.price_history(session, asset_id, grade, days)}
