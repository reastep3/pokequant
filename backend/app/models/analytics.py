"""Derived data: analytics snapshots (screener source of truth), signals, ingestion audit."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalKind(str, enum.Enum):
    GRADING_ARB = "grading_arb"
    JP_EN_PAIRS = "jp_en_pairs"
    SEALED_EV = "sealed_ev"
    BUBBLE = "bubble"
    DRAWDOWN_RECOVERY = "drawdown_recovery"


class SignalStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    TRIGGERED = "triggered"


class AnalyticsSnapshot(Base):
    """One row per (asset, grade, run). The screener reads the latest row per asset/grade.

    Columns are deliberately denormalized so the screener is a single index-only scan.
    """

    __tablename__ = "analytics_snapshots"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    grade_key: Mapped[str] = mapped_column(String(12), primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True)

    market_price: Mapped[float | None]
    fair_value: Mapped[float | None]  # kernel-regression trendline value at t
    fair_value_lo: Mapped[float | None]
    fair_value_hi: Mapped[float | None]
    ev_gap_pct: Mapped[float | None]  # (fair - market) / market

    ret_30d: Mapped[float | None]
    vol_90d_ann: Mapped[float | None]
    dd_30d: Mapped[float | None]
    dd_60d: Mapped[float | None]
    dd_90d: Mapped[float | None]
    recovery_30d_days: Mapped[int | None]  # None => not yet recovered
    recovery_60d_days: Mapped[int | None]
    recovery_90d_days: Mapped[int | None]
    underwater_days: Mapped[int | None]

    kelly_fraction: Mapped[float | None]
    kelly_fraction_scaled: Mapped[float | None]  # after multiplier + cap
    kelly_growth_rate: Mapped[float | None]
    expected_return: Mapped[float | None]
    kelly_adj_roi: Mapped[float | None]  # kelly_fraction_scaled * expected_return
    liquidity_score: Mapped[float | None]  # 0..100
    screener_score: Mapped[float | None]

    arb_spread: Mapped[float | None]  # E[graded value] - (raw + fees)
    arb_spread_z: Mapped[float | None]
    pairs_z: Mapped[float | None]
    pairs_half_life_days: Mapped[float | None]

    cs_fundamental_value: Mapped[float | None]
    cs_bubble_premium_pct: Mapped[float | None]
    cs_bubble_share: Mapped[float | None]  # variance share attributable to non-fundamental component

    pop_total: Mapped[int | None]
    pop_gem_rate: Mapped[float | None]
    pop_velocity_30d: Mapped[float | None]  # new slabs / day
    pop_momentum_z: Mapped[float | None]

    sealed_ev: Mapped[float | None]
    sealed_ev_ratio: Mapped[float | None]
    sealed_p_profit: Mapped[float | None]

    payload: Mapped[dict | None]  # full engine output (curves, distributions) for the asset page

    __table_args__ = (Index("ix_analytics_snapshots_latest", "asset_id", "grade_key", ts.desc()),)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    grade_key: Mapped[str | None] = mapped_column(String(12))
    kind: Mapped[SignalKind] = mapped_column(Enum(SignalKind, name="signal_kind"))
    direction: Mapped[str] = mapped_column(String(8))  # long | short | flat
    strength: Mapped[float]  # z-score or normalized edge
    headline: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None]
    status: Mapped[SignalStatus] = mapped_column(Enum(SignalStatus, name="signal_status"), default=SignalStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_signals_status_created", "status", created_at.desc()),)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error
    rows_written: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None]
