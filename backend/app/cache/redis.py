"""Redis-backed cache + distributed locks with an in-process fallback (local dev / tests)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import orjson

from app.config import get_settings

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[assignment,misc]


class Cache:
    def __init__(self, url: str | None):
        self._redis = Redis.from_url(url, decode_responses=False) if (url and Redis) else None
        self._mem: dict[str, tuple[float, bytes]] = {}
        self._locks: dict[str, float] = {}
        self._pubsub_subs: dict[str, list[asyncio.Queue]] = {}

    @property
    def backend(self) -> str:
        return "redis" if self._redis else "memory"

    async def get_json(self, key: str) -> Any | None:
        if self._redis:
            raw = await self._redis.get(key)
        else:
            item = self._mem.get(key)
            raw = item[1] if item and item[0] > time.time() else None
        return orjson.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        raw = orjson.dumps(value, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)
        if self._redis:
            await self._redis.set(key, raw, ex=ttl)
        else:
            self._mem[key] = (time.time() + ttl, raw)

    async def delete(self, *keys: str) -> None:
        if self._redis:
            await self._redis.delete(*keys)
        else:
            for k in keys:
                self._mem.pop(k, None)

    async def publish(self, channel: str, value: Any) -> None:
        raw = orjson.dumps(value, option=orjson.OPT_SERIALIZE_NUMPY)
        if self._redis:
            await self._redis.publish(channel, raw)
        else:
            for q in self._pubsub_subs.get(channel, []):
                q.put_nowait(raw)

    async def subscribe(self, channel: str) -> AsyncIterator[bytes]:
        if self._redis:
            ps = self._redis.pubsub()
            await ps.subscribe(channel)
            try:
                async for msg in ps.listen():
                    if msg.get("type") == "message":
                        yield msg["data"]
            finally:
                await ps.unsubscribe(channel)
                await ps.aclose()
        else:
            q: asyncio.Queue = asyncio.Queue()
            self._pubsub_subs.setdefault(channel, []).append(q)
            try:
                while True:
                    yield await q.get()
            finally:
                self._pubsub_subs[channel].remove(q)

    @contextlib.asynccontextmanager
    async def lock(self, name: str, ttl: int = 600) -> AsyncIterator[bool]:
        """SET NX EX lock. Yields True if acquired; releases only if still the owner."""
        key = f"lock:{name}"
        token = uuid.uuid4().hex
        acquired = False
        if self._redis:
            acquired = bool(await self._redis.set(key, token, nx=True, ex=ttl))
        else:
            exp = self._locks.get(key, 0)
            if exp < time.time():
                self._locks[key] = time.time() + ttl
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                if self._redis:
                    await self._redis.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                        1, key, token,
                    )
                else:
                    self._locks.pop(key, None)

    async def ping(self) -> bool:
        if not self._redis:
            return True
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        if self._redis:
            await self._redis.aclose()


@lru_cache
def get_cache() -> Cache:
    return Cache(get_settings().redis_url)
