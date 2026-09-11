"""Sealed SKUs and the pull-rate tables that feed the EV matrix."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SealedProduct(Base):
    __tablename__ = "sealed_products"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("card_sets.id", ondelete="CASCADE"))
    product_type: Mapped[str] = mapped_column(String(32))  # booster_box | etb | booster_bundle | case
    packs_per_unit: Mapped[int]
    cards_per_pack: Mapped[int] = mapped_column(default=10)
    msrp: Mapped[float | None] = mapped_column(Numeric(10, 2))
    bulk_value_per_pack: Mapped[float] = mapped_column(Numeric(8, 2), default=0.35)
    meta: Mapped[dict | None]

    asset = relationship("Asset", back_populates="sealed")


class PullRate(Base):
    """Per-set pull-rate slot.

    Either `asset_id` is set (a specific chase card, e.g. 1:1200 packs) or `rarity_tier`
    describes a tier with `n_cards_in_tier` equally-likely cards; the EV engine expands the
    tier using each member card's current RAW price.
    """

    __tablename__ = "pull_rates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("card_sets.id", ondelete="CASCADE"))
    slot_name: Mapped[str] = mapped_column(String(64))  # "SIR", "SAR", "IR", "Gold", "Ultra Rare"
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    rarity_tier: Mapped[str | None] = mapped_column(String(48))
    n_cards_in_tier: Mapped[int | None]
    prob_per_pack: Mapped[float] = mapped_column(Numeric(10, 8))  # P(slot hit in one pack)
    source: Mapped[str] = mapped_column(String(48), default="community")
    sample_packs: Mapped[int | None]
    observed_at: Mapped[datetime | None]
    meta: Mapped[dict | None]

    __table_args__ = (Index("ix_pull_rates_set_slot", "set_id", "slot_name"),)
