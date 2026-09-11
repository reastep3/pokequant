from fastapi import APIRouter

from app.api import admin, assets, health, screener, sealed, signals, stream

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(screener.router, tags=["screener"])
api_router.include_router(assets.router, tags=["assets"])
api_router.include_router(sealed.router, tags=["sealed"])
api_router.include_router(signals.router, tags=["signals"])
api_router.include_router(stream.router, tags=["stream"])
api_router.include_router(admin.router, tags=["admin"])
health_router = health.router
