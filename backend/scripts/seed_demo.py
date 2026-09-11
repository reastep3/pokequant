"""Seed a demo universe (EN/JP card pairs + sealed SKUs), ingest synthetic history, run analytics.

    python -m scripts.seed_demo            # SQLite or whatever DATABASE_URL points at
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker
from app.ingestion.normalization import CardSpec, canonical_key, rarity_class, resolve_ja_to_en
from app.ingestion.pipeline import run_pop_ingest, run_price_ingest
from app.logging_config import configure_logging
from app.models import (
    Asset,
    AssetType,
    CardSet,
    Language,
    PrintEquivalence,
    PullRate,
    SealedProduct,
)
from app.models.asset import EquivalenceType
from app.services.analytics_service import run_analytics

log = logging.getLogger("seed")

SETS = [
    ("sv3pt5", "Scarlet & Violet—151", "en", date(2023, 9, 22), None),
    ("sv2a", "Pokémon Card 151", "ja", date(2023, 6, 16), "sv3pt5"),
    ("swsh7", "Evolving Skies", "en", date(2021, 8, 27), None),
    ("s6a", "Eevee Heroes", "ja", date(2021, 5, 28), "swsh7"),
    ("swsh12pt5", "Crown Zenith", "en", date(2023, 1, 20), None),
    ("s12a", "VSTAR Universe", "ja", date(2022, 12, 2), "swsh12pt5"),
    ("sv8pt5", "Prismatic Evolutions", "en", date(2025, 1, 17), None),
    ("sv8a", "Terastal Festival ex", "ja", date(2024, 12, 6), "sv8pt5"),
    ("base1", "Base Set", "en", date(1999, 1, 9), None),
    ("svp", "SV Black Star Promos", "en", date(2023, 3, 31), None),
    ("SV-P", "SV Promo (JP)", "ja", date(2023, 1, 20), None),
]

# (name, en_set, en_number, ja_set, ja_number, rarity_en, rarity_ja, base_psa10_usd, pop, liquidity)
CARDS = [
    ("Charizard ex", "sv3pt5", "199/165", "sv2a", "201/165", "SIR", "SAR", 1400, 9000, 0.6),
    ("Mew ex", "sv3pt5", "205/165", "sv2a", "205/165", "SIR", "SAR", 260, 6000, 0.5),
    ("Venusaur ex", "sv3pt5", "198/165", "sv2a", "200/165", "SIR", "SAR", 210, 3000, 0.4),
    ("Blastoise ex", "sv3pt5", "200/165", "sv2a", "202/165", "SIR", "SAR", 230, 3200, 0.4),
    ("Umbreon VMAX", "swsh7", "215/203", "s6a", "095/069", "Alternate Art", "SA", 1900, 7000, 0.7),
    ("Rayquaza VMAX", "swsh7", "218/203", "s7r", "080/067", "Alternate Art", "SA", 720, 4500, 0.5),
    ("Sylveon VMAX", "swsh7", "212/203", "s6a", "093/069", "Alternate Art", "SA", 480, 3800, 0.5),
    ("Glaceon VMAX", "swsh7", "209/203", "s6a", "091/069", "Alternate Art", "SA", 380, 3100, 0.4),
    ("Pikachu VMAX", "swsh12pt5", "GG52/GG70", "s12a", "224/172", "Galarian Gallery", "SAR", 310, 5200, 0.5),
    ("Giratina VSTAR", "swsh12pt5", "GG69/GG70", "s12a", "261/172", "Galarian Gallery", "UR", 260, 4100, 0.45),
    ("Umbreon ex", "sv8pt5", "161/131", "sv8a", "230/187", "SIR", "SAR", 1650, 5500, 0.65),
    ("Eevee ex", "sv8pt5", "167/131", "sv8a", "233/187", "SIR", "SAR", 420, 3900, 0.5),
    ("Charizard", "base1", "4/102", None, None, "Holo Rare", None, 4200, 1200, 0.3),
    ("Blastoise", "base1", "2/102", None, None, "Holo Rare", None, 1100, 1500, 0.3),
    ("Pikachu", "svp", "085", "SV-P", "208", "Promo", "Promo", 800, 2500, 0.55),  # Van Gogh vs Yu Nagaba
]

SEALED = [
    ("Scarlet & Violet—151 Booster Bundle", "sv3pt5", "booster_bundle", 6, 55.0, 0.35),
    ("Scarlet & Violet—151 Elite Trainer Box", "sv3pt5", "etb", 9, 110.0, 0.35),
    ("Pokémon Card 151 Booster Box (JP)", "sv2a", "booster_box", 20, 95.0, 0.15),
    ("Evolving Skies Booster Box", "swsh7", "booster_box", 36, 1200.0, 0.4),
    ("Crown Zenith Elite Trainer Box", "swsh12pt5", "etb", 10, 95.0, 0.4),
    ("Prismatic Evolutions Booster Bundle", "sv8pt5", "booster_bundle", 6, 70.0, 0.35),
    ("Terastal Festival ex Booster Box (JP)", "sv8a", "booster_box", 20, 140.0, 0.15),
]

# per-set pull slots: (slot, rarity_tier, prob_per_pack, avg_value_if_no_cards)
PULLS = {
    "sv3pt5": [("SIR", "SIR", 1 / 130, None), ("IR", "IR", 1 / 9, 6.0), ("Ultra Rare", "UR", 1 / 14, 4.0)],
    "sv2a": [("SAR", "SAR", 1 / 100, None), ("AR", "AR", 1 / 5, 2.5), ("SR", "SR", 1 / 10, 3.0)],
    "swsh7": [("Alt Art", "Alternate Art", 1 / 190, None), ("VMAX", "VMAX", 1 / 36, 12.0), ("V", "V", 1 / 8, 3.0)],
    "s6a": [("SA", "SA", 1 / 120, None), ("SR", "SR", 1 / 20, 8.0)],
    "swsh12pt5": [("GG", "Galarian Gallery", 1 / 8, None), ("Gold", "Gold", 1 / 90, 40.0)],
    "s12a": [("SAR", "SAR", 1 / 60, None), ("AR", "AR", 1 / 3, 2.0), ("UR", "UR", 1 / 200, 60.0)],
    "sv8pt5": [("SIR", "SIR", 1 / 210, None), ("IR", "IR", 1 / 12, 8.0), ("ACE SPEC", "ACE SPEC", 1 / 20, 5.0)],
    "sv8a": [("SAR", "SAR", 1 / 90, None), ("AR", "AR", 1 / 5, 3.0)],
}


def slug(s: str) -> str:
    return "".join(ch for ch in s.upper().replace(" ", "-") if ch.isalnum() or ch == "-")[:18]


async def seed() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    if settings.is_sqlite:
        import app.models  # noqa: F401
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as session:
        if (await session.execute(select(Asset.id).limit(1))).scalar():
            log.info("assets already present; skipping reference seed")
        else:
            sets: dict[str, CardSet] = {}
            for code, name, lang, rel, en_eq in SETS:
                cs = CardSet(code=code, name=name, language=Language(lang), release_date=rel, en_equivalent_code=en_eq)
                session.add(cs)
                sets[code] = cs
            for extra in ("s7r",):
                cs = CardSet(code=extra, name="Blue Sky Stream", language=Language.JA, release_date=date(2021, 7, 9), en_equivalent_code="swsh7")
                session.add(cs)
                sets[extra] = cs
            await session.flush()

            for name, en_set, en_num, ja_set, ja_num, rar_en, rar_ja, base, pop, liq in CARDS:
                en_spec = CardSpec(name, en_set, en_num, rar_en, "en", str(sets[en_set].release_date))
                en_asset = Asset(ticker=f"{slug(name)}-{en_set.upper()}-EN", asset_type=AssetType.CARD, name=name,
                                 language=Language.EN, set_id=sets[en_set].id, number=en_num, rarity=rar_en,
                                 canonical_key=canonical_key(name, en_set, en_num, rarity_class(rar_en, "en")),
                                 external_ids={"synthetic_base": base, "synthetic_pop": pop, "synthetic_liquidity": liq})
                session.add(en_asset)
                await session.flush()
                if ja_set:
                    ja_spec = CardSpec(name, ja_set, ja_num, rar_ja, "ja", str(sets[ja_set].release_date))
                    res = resolve_ja_to_en(ja_spec, [en_spec])
                    ja_asset = Asset(ticker=f"{slug(name)}-{ja_set.upper()}-JA", asset_type=AssetType.CARD, name=name,
                                     language=Language.JA, set_id=sets[ja_set].id, number=ja_num, rarity=rar_ja,
                                     canonical_key=res.canonical_key,
                                     external_ids={"synthetic_base": base * 0.45, "synthetic_pop": int(pop * 0.6), "synthetic_liquidity": liq * 0.8},
                                     attrs={"equivalence": res.equivalence, "notes": res.notes})
                    session.add(ja_asset)
                    await session.flush()
                    session.add(PrintEquivalence(ja_asset_id=ja_asset.id, en_asset_id=en_asset.id if res.en_candidate else None,
                                                 equivalence=EquivalenceType(res.equivalence), confidence=res.confidence, notes=res.notes))

            ptype_abbr = {"booster_box": "BB", "etb": "ETB", "booster_bundle": "BDL", "case": "CASE"}
            for name, set_code, ptype, packs, base, bulk in SEALED:
                a = Asset(ticker=f"{set_code.upper()}-{ptype_abbr.get(ptype, ptype.upper()[:3])}-{sets[set_code].language.value.upper()}",
                          asset_type=AssetType.SEALED, name=name,
                          language=sets[set_code].language, set_id=sets[set_code].id,
                          canonical_key=f"sealed:{set_code}:{ptype}", external_ids={"synthetic_base": base, "sealed": True})
                session.add(a)
                await session.flush()
                session.add(SealedProduct(asset_id=a.id, set_id=sets[set_code].id, product_type=ptype, packs_per_unit=packs,
                                          msrp=base * 0.6, bulk_value_per_pack=bulk))
            for set_code, slots in PULLS.items():
                for slot, tier, p, avg in slots:
                    session.add(PullRate(set_id=sets[set_code].id, slot_name=slot, rarity_tier=tier, prob_per_pack=p,
                                         source="community-sample", meta={"avg_value": avg} if avg else None))
            await session.commit()
            log.info("reference data seeded")

        n = await run_price_ingest(session, settings)
        log.info("price ingest rows=%s", n)
        n = await run_pop_ingest(session, settings)
        log.info("pop ingest rows=%s", n)
        n = await run_analytics(session, settings)
        log.info("analytics snapshots=%s", n)


if __name__ == "__main__":
    asyncio.run(seed())
