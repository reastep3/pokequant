"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app import __version__
from app.api.router import api_router, health_router
from app.cache import get_cache
from app.config import get_settings
from app.db.session import get_engine
from app.logging_config import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    if s.is_sqlite:  # zero-setup local dev; PostgreSQL uses Alembic (see release_command in fly.toml)
        import app.models  # noqa: F401
        from app.db.base import Base
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    log.info("pokequant api %s starting (env=%s, demo=%s, cache=%s)", __version__, s.app_env, s.demo_mode, get_cache().backend)
    yield
    await get_cache().aclose()
    await get_engine().dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="PokéQuant API", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if s.app_env != "production" else None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
