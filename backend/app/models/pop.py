"""Grading population reports (PSA / BGS / CGC) sampled over time.

Each row is a full snapshot so velocity = Δtotal / Δt is a pure read-side computation and
the underlying scrape stays idempotent.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PopReport(Base):
    __tablename__ = "pop_reports"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    grader: Mapped[str] = mapped_column(String(8), primary_key=True)  # PSA | BGS | CGC
    ts: Mapped[datetime] = mapped_column(primary_key=True)

    # {"10": 1234, "9": 4567, "8": 890, ..., "Q": qualifiers}
    grade_counts: Mapped[dict]
    total: Mapped[int]
    gem_rate: Mapped[float | None]  # share of 10s (PSA) / 9.5+ (BGS/CGC) at snapshot time
    source: Mapped[str] = mapped_column(String(24), default="parsebot")
    meta: Mapped[dict | None]

    __table_args__ = (Index("ix_pop_reports_asset_grader_ts", "asset_id", "grader", ts.desc()),)
