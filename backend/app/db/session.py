"""Async engine/session factory + dialect-aware bulk upsert."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from functools import lru_cache
from typing import Any

import orjson
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    s = get_settings()
    kwargs: dict[str, Any] = {
        "echo": s.db_echo,
        "pool_pre_ping": True,
        "json_serializer": lambda o: orjson.dumps(o, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS).decode(),
        "json_deserializer": orjson.loads,
    }
    if s.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_recycle=1800)
    return create_async_engine(s.database_url, **kwargs)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def upsert(
    session: AsyncSession,
    model: type,
    rows: Iterable[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str] | None = None,
    chunk: int = 250,
    commit_each: bool = False,
) -> int:
    """INSERT ... ON CONFLICT for both PG and SQLite. Returns rows submitted.

    Rows are de-duplicated on the conflict key (last wins): PostgreSQL rejects a batch that
    touches the same row twice under ON CONFLICT DO UPDATE. ``commit_each`` commits per chunk so
    bulk backfills don't hold one giant transaction open against a small database machine.
    """
    dedup: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        dedup[tuple(r.get(c) for c in conflict_cols)] = r
    rows = list(dedup.values())
    if not rows:
        return 0
    # Multi-row VALUES requires a uniform key set; pad sparse dicts with NULLs.
    keys = sorted({k for r in rows for k in r})
    rows = [{k: r.get(k) for k in keys} for r in rows]
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    n = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i : i + chunk]
        stmt = insert(model).values(batch)
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_={c: getattr(stmt.excluded, c) for c in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        await session.execute(stmt)
        n += len(batch)
        if commit_each:
            await session.commit()
    return n
