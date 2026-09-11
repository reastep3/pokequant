"""Asyncio job scheduler for the Fly `worker` process group.

Each job runs on a fixed interval with jitter, guarded by a Redis lock so scaling the worker
group horizontally never double-runs an ingest. Jobs are independent tasks: a slow pop scrape
does not delay analytics.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.api.stream import CHANNEL
from app.cache import get_cache
from app.config import get_settings
from app.db.session import get_sessionmaker
from app.ingestion.pipeline import run_pop_ingest, run_price_ingest
from app.logging_config import configure_logging
from app.services import screener_service
from app.services.analytics_service import run_analytics

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    fn: Callable[..., Awaitable[int]]
    interval_s: int
    lock_ttl_s: int = 3600
    publish: bool = False


async def _run_once(job: Job) -> None:
    cache = get_cache()
    async with cache.lock(f"job:{job.name}", ttl=job.lock_ttl_s) as ok:
        if not ok:
            log.info("job %s skipped: lock held by another worker", job.name)
            return
        started = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            n = await job.fn(session)
        log.info("job %s done rows=%s in %.1fs", job.name, n, (datetime.now(UTC) - started).total_seconds())
        if job.publish:
            await screener_service.invalidate(cache)
            await cache.publish(CHANNEL, {"job": job.name, "rows": n, "ts": datetime.now(UTC).isoformat()})


async def _loop(job: Job, stop: asyncio.Event) -> None:
    await asyncio.sleep(random.uniform(0, 5))
    while not stop.is_set():
        try:
            await _run_once(job)
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", job.name)
        wait = job.interval_s * random.uniform(0.95, 1.05)
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except TimeoutError:
            pass


def default_jobs() -> list[Job]:
    s = get_settings()
    return [
        Job("price_ingest", run_price_ingest, s.price_ingest_interval_s, lock_ttl_s=3600),
        Job("pop_ingest", run_pop_ingest, s.pop_ingest_interval_s, lock_ttl_s=3600),
        Job("analytics", run_analytics, s.analytics_interval_s, lock_ttl_s=1800, publish=True),
    ]


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    if not s.worker_enabled:
        log.warning("WORKER_ENABLED=false; idling")
        await asyncio.Event().wait()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    jobs = default_jobs()
    log.info("worker up: %s", [(j.name, j.interval_s) for j in jobs])
    await asyncio.gather(*(_loop(j, stop) for j in jobs))
    await get_cache().aclose()


if __name__ == "__main__":
    asyncio.run(main())
