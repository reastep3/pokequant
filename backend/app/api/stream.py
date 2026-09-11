"""Server-Sent Events: pushes a screener refresh whenever the worker publishes new analytics,
with a heartbeat so proxies (Fly's edge included) keep the connection open."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CacheDep, SessionDep, SettingsDep
from app.db.session import get_sessionmaker
from app.services import screener_service

router = APIRouter()
CHANNEL = "analytics:updated"


@router.get("/stream/screener")
async def stream_screener(session: SessionDep, cache: CacheDep, settings: SettingsDep, sort: str = "screener_score") -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        snapshot = await screener_service.screener(session, cache, sort=sort, ttl=settings.cache_ttl_s)
        yield b"event: screener\ndata: " + orjson.dumps(snapshot) + b"\n\n"
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        async def pump() -> None:
            async for msg in cache.subscribe(CHANNEL):
                await queue.put(msg)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                # Request-scoped sessions are closed before a StreamingResponse starts sending
                # (FastAPI >= 0.106), so every refresh opens its own short-lived session.
                async with get_sessionmaker()() as fresh_session:
                    fresh = await screener_service.screener(fresh_session, cache, sort=sort, ttl=settings.cache_ttl_s)
                yield b"event: screener\ndata: " + orjson.dumps(fresh) + b"\n\n"
                yield b"event: update\ndata: " + msg + b"\n\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
