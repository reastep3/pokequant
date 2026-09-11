from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.services import screener_service

router = APIRouter()


@router.get("/signals")
async def list_signals(session: SessionDep, status: str = Query("active", pattern="^(active|expired|triggered)$"),
                       limit: int = Query(100, ge=1, le=500)) -> dict:
    rows = await screener_service.signals(session, status=status, limit=limit)
    return {"count": len(rows), "rows": rows}
