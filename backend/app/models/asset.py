"""Reference data: sets, assets (cards + sealed), and JP<->EN print equivalence."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AssetType(str, enum.Enum):
    CARD = "card"
    SEALED = "sealed"


class Language(str, enum.Enum):
    EN = "en"
    JA = "ja"


class EquivalenceType(str, enum.Enum):
    IDENTICAL = "identical"  # same art/number semantics, same rarity
    REPRINT = "reprint"  # same card, different set/number
    PROMO_EXCLUSIVE = "promo_exclusive"  # JP promo w/ no EN print; EN proxy is nearest analogue
    NO_EQUIVALENT = "no_equivalent"


class CardSet(Base):
    __tablename__ = "card_sets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. "sv3pt5", "sv2a"
    name: Mapped[str] = mapped_column(String(160))
    language: Mapped[Language] = mapped_column(Enum(Language, name="language"))
    series: Mapped[str | None] = mapped_column(String(80))
    release_date: Mapped[date | None] = mapped_column(Date)
    total_cards: Mapped[int | None]
    # For JP sets: the EN set(s) that carry the same print run. Many-to-one is enough
    # for the common case; split sets (e.g. s7r + s7d -> swsh7) both point at swsh7.
    en_equivalent_code: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict | None]

    assets: Mapped[list[Asset]] = relationship(back_populates="set")


class Asset(Base):
    """A tradable line item: a specific card print or a sealed SKU.

    `ticker` is the human-facing terminal symbol (e.g. CHARIZARD-BS-4-EN).
    `canonical_key` collapses JP/EN prints of the same card so pairs logic can join them.
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(48), unique=True)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType, name="asset_type"))
    name: Mapped[str] = mapped_column(String(160))
    language: Mapped[Language] = mapped_column(Enum(Language, name="language"))
    set_id: Mapped[int | None] = mapped_column(ForeignKey("card_sets.id", ondelete="SET NULL"))
    number: Mapped[str | None] = mapped_column(String(24))  # "4/102", "025/165", "SV-P 208"
    rarity: Mapped[str | None] = mapped_column(String(48))
    canonical_key: Mapped[str] = mapped_column(String(160), index=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    external_ids: Mapped[dict | None]  # {"ppt": "...", "psa_spec": "...", "tcgplayer": 123}
    attrs: Mapped[dict | None]  # free-form (artist, holo pattern, promo campaign ...)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    set: Mapped[CardSet | None] = relationship(back_populates="assets")
    sealed: Mapped[SealedProduct | None] = relationship(
        back_populates="asset", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_assets_type_lang", "asset_type", "language"),)


class PrintEquivalence(Base):
    """Explicit JP -> EN mapping produced by the normalization layer (or curated)."""

    __tablename__ = "print_equivalence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ja_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    en_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    equivalence: Mapped[EquivalenceType] = mapped_column(Enum(EquivalenceType, name="equivalence_type"))
    confidence: Mapped[float] = mapped_column(default=1.0)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_print_equivalence_pair", "ja_asset_id", "en_asset_id", unique=True),)


from app.models.sealed import SealedProduct  # noqa: E402  (circular import for typing)
