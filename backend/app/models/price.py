"""Time-series price data.

`price_ticks` is the wide daily/hourly bar table (one row per asset × grade × source × ts).
On PostgreSQL the Alembic migration converts it to a TimescaleDB hypertable with
compression; on plain PG it falls back to a BRIN index on `ts`. `sales` holds raw
individual transactions used by the kernel-regression fair-value model.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# grade_key vocabulary: RAW | PSA10 | PSA9 | PSA8 | BGS10 | BGS9.5 | CGC10 | CGC9.5 | SEALED
GRADE_KEY_LEN = 12


class PriceTick(Base):
    __tablename__ = "price_ticks"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    grade_key: Mapped[str] = mapped_column(String(GRADE_KEY_LEN), primary_key=True)
    source: Mapped[str] = mapped_column(String(24), primary_key=True)  # ppt | parsebot | ebay | agg
    ts: Mapped[datetime] = mapped_column(primary_key=True)

    price: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    low: Mapped[float | None] = mapped_column(Numeric(14, 2))
    high: Mapped[float | None] = mapped_column(Numeric(14, 2))
    volume: Mapped[int | None]  # completed sales in window
    n_listings: Mapped[int | None]  # active supply (depth proxy)
    bid: Mapped[float | None] = mapped_column(Numeric(14, 2))  # best offer proxy if the oracle exposes one
    ask: Mapped[float | None] = mapped_column(Numeric(14, 2))
    meta: Mapped[dict | None]

    __table_args__ = (
        # Hot query path: latest N bars for (asset, grade). ts DESC keeps the index scan short.
        Index("ix_price_ticks_asset_grade_ts", "asset_id", "grade_key", ts.desc()),
        Index("ix_price_ticks_ts_brin", "ts", postgresql_using="brin"),
    )


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    grade_key: Mapped[str] = mapped_column(String(GRADE_KEY_LEN))
    ts: Mapped[datetime] = mapped_column()
    price: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    source: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str | None] = mapped_column(String(96))
    cert_number: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict | None]

    __table_args__ = (
        Index("ix_sales_asset_grade_ts", "asset_id", "grade_key", ts.desc()),
        Index("ix_sales_source_ext", "source", "external_id", unique=True),
    )
