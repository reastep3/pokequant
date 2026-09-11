"""Oracle abstractions: typed payloads, rate limiting, retrying HTTP client."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetRef:
    """What an oracle needs to identify a card: our asset id + its external ids."""

    asset_id: int
    ticker: str
    external_ids: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    set_code: str | None = None
    number: str | None = None


@dataclass(frozen=True)
class PricePoint:
    asset_id: int
    grade_key: str
    ts: datetime
    price: float
    source: str
    currency: str = "USD"
    low: float | None = None
    high: float | None = None
    volume: int | None = None
    n_listings: int | None = None
    bid: float | None = None
    ask: float | None = None
    meta: dict | None = None


@dataclass(frozen=True)
class SaleRecord:
    asset_id: int
    grade_key: str
    ts: datetime
    price: float
    source: str
    external_id: str | None = None
    cert_number: str | None = None
    currency: str = "USD"
    meta: dict | None = None


@dataclass(frozen=True)
class PopSnapshot:
    asset_id: int
    grader: str
    ts: datetime
    grade_counts: dict[str, int]
    total: int
    source: str
    meta: dict | None = None


class PriceOracle(Protocol):
    name: str

    async def fetch_prices(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[PricePoint]: ...
    async def fetch_sales(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[SaleRecord]: ...


class PopOracle(Protocol):
    name: str

    async def fetch_pop(self, refs: Sequence[AssetRef]) -> list[PopSnapshot]: ...


class TokenBucket:
    """Simple asyncio token bucket: ``rate`` tokens/second, burst ``capacity``."""

    def __init__(self, rate: float, capacity: int | None = None):
        self.rate = rate
        self.capacity = capacity or max(1, int(rate * 2))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class OracleClient:
    """httpx wrapper with bearer auth, token-bucket throttling and exponential backoff on 429/5xx."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, api_key: str | None, *, rps: float = 2.0, timeout: float = 20.0,
                 max_retries: int = 4, auth_header: str = "Authorization", auth_prefix: str = "Bearer "):
        headers = {"User-Agent": "pokequant/0.1 (+https://fly.io)"}
        if api_key:
            headers[auth_header] = f"{auth_prefix}{api_key}" if auth_prefix else api_key
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout,
                                         limits=httpx.Limits(max_connections=8))
        self._bucket = TokenBucket(rps)
        self.max_retries = max_retries

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            await self._bucket.acquire()
            try:
                r = await self._client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt == self.max_retries:
                    raise
                log.warning("oracle transport error %s on %s (attempt %d)", e, path, attempt + 1)
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code not in self.RETRY_STATUS or attempt == self.max_retries:
                    r.raise_for_status()
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
                log.warning("oracle %s on %s, backing off %.1fs", r.status_code, path, delay)
            await asyncio.sleep(delay + random.uniform(0, 0.25))
            delay = min(delay * 2, 30)
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_ts(value: Any) -> datetime:
    from datetime import UTC
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), tz=UTC)
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def dig(obj: Any, *paths: str, default: Any = None) -> Any:
    """Return the first non-null value found at any dotted path (tolerant JSON extraction)."""
    for path in paths:
        cur = obj
        ok = True
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
                cur = cur[int(key)]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return default
