"""Declarative base + portable column types.

JSONB / TIMESTAMPTZ on PostgreSQL, generic JSON / DateTime on SQLite so the same
models drive local tests and production.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class TZDateTime(TypeDecorator):
    """TIMESTAMPTZ on PostgreSQL; UTC-normalized naive storage on SQLite. Always *returns* aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {dict: PortableJSON, list: PortableJSON, datetime: TZDateTime()}
