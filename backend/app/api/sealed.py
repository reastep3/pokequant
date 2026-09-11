from fastapi import APIRouter

from app.api.deps import SessionDep
from app.services import screener_service

router = APIRouter()


@router.get("/sealed/ev")
async def sealed_ev_matrix(session: SessionDep) -> dict:
    rows = await screener_service.sealed_matrix(session)
    return {"count": len(rows), "rows": rows}
