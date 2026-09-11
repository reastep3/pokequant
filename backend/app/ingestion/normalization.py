"""JP <-> EN normalization.

Goal: give every print a ``canonical_key`` so Japanese and English versions of the same card
land on the same key (for pairs analytics), and classify how a JP print maps to EN:

  * identical        – same set print run (JP sv2a "Pokemon Card 151" ↔ EN sv3pt5 "151"), same rarity class
  * reprint          – same card art in a different EN set (split JP sets, subsequent EN reprints)
  * promo_exclusive  – JP promo with no EN print; an EN *proxy* is attached for cross-market signals
  * no_equivalent    – nothing sensible to pair against

Rules are data-driven from ``seed/print_equivalence.json`` so new sets do not require code changes.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SEED_PATH = Path(__file__).parent / "seed" / "print_equivalence.json"

SUFFIX_CANON = [
    (r"\bvmax\b", "vmax"), (r"\bvstar\b", "vstar"), (r"\bv-union\b", "vunion"), (r"\bv\b", "v"),
    (r"\bex\b", "ex"), (r"\bgx\b", "gx"), (r"\btag team\b", "tagteam"), (r"\bδ\b", "delta"),
    (r"\bshining\b", "shining"), (r"\bdark\b", "dark"), (r"\blight\b", "light"),
]
NOISE = re.compile(r"[’'’`\"\-\.\(\)\[\],!:&]+")


@lru_cache
def load_seed() -> dict:
    with SEED_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    """Lower-cased, de-accented, alias-resolved base name with canonical mechanic suffix."""
    s = strip_diacritics(name).lower().strip()
    s = NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    aliases = load_seed()["name_aliases"]
    tokens = s.split(" ")
    if tokens and tokens[0] in aliases:
        tokens[0] = aliases[tokens[0]].lower()
    s = " ".join(tokens)
    s = s.replace("pokemon ", "").replace("poke ", "")
    for pat, canon in SUFFIX_CANON:
        s = re.sub(pat, canon, s)
    return re.sub(r"\s+", "", s)


def normalize_number(number: str | None) -> str | None:
    if not number:
        return None
    s = number.strip().upper().replace(" ", "")
    m = re.match(r"^0*(\d+)(?:/\d+)?$", s)
    if m:
        return m.group(1)
    return s  # promo formats like "SV-P208", "SWSH039"


def rarity_class(rarity: str | None, language: str) -> str | None:
    if not rarity:
        return None
    r = rarity.strip()
    if language == "ja":
        return load_seed()["rarity_map"].get(r.upper(), r)
    return r


def en_sets_for(ja_set_code: str) -> list[str]:
    return load_seed()["set_map"].get(ja_set_code.lower(), [])


def canonical_key(name: str, en_set_code: str | None, number: str | None, rarity_cls: str | None) -> str:
    """Stable join key: name + EN set (or JP set if unmapped) + number + rarity class."""
    parts = [normalize_name(name), (en_set_code or "unmapped").lower(), normalize_number(number) or "?",
             (rarity_cls or "?").lower().replace(" ", "")]
    return ":".join(parts)


@dataclass(frozen=True)
class CardSpec:
    name: str
    set_code: str
    number: str | None
    rarity: str | None
    language: str  # "en" | "ja"
    release_date: str | None = None  # ISO
    artist: str | None = None


@dataclass(frozen=True)
class Resolution:
    equivalence: str  # identical | reprint | promo_exclusive | no_equivalent
    en_candidate: CardSpec | None
    confidence: float
    canonical_key: str
    notes: str = ""


def is_promo_set(set_code: str) -> bool:
    s = set_code.upper()
    return s.endswith("-P") or "PROMO" in s or s in {"SVP", "SWSHP", "SMP", "XYP", "BWP"}


def resolve_ja_to_en(ja: CardSpec, en_pool: list[CardSpec]) -> Resolution:
    """Map a Japanese print onto the English universe.

    ``en_pool`` should contain EN cards with the same normalized name (pre-filtered by caller).
    """
    seed = load_seed()
    nname = normalize_name(ja.name)
    nnum = normalize_number(ja.number)
    rcls = rarity_class(ja.rarity, "ja")

    # 1) explicit curated overrides (promos, special cases)
    for ov in seed["promo_overrides"]:
        if ov["ja_set"].upper() == ja.set_code.upper() and normalize_number(ov["ja_number"]) == nnum \
                and normalize_name(ov["name"]) == nname:
            proxy = ov.get("en_proxy")
            cand = None
            if proxy:
                cand = next((c for c in en_pool if c.set_code.lower() == proxy["set"].lower()
                             and normalize_number(c.number) == normalize_number(proxy["number"])), None)
            key = canonical_key(ja.name, proxy["set"] if proxy else None, proxy["number"] if proxy else ja.number, rcls)
            return Resolution(ov["equivalence"], cand, 1.0 if (cand or not proxy) else 0.5, key, ov.get("notes", ""))

    # 2) mapped set: same number & rarity class => identical; same rarity class => reprint
    en_sets = en_sets_for(ja.set_code)
    if en_sets:
        same_set = [c for c in en_pool if c.set_code.lower() in en_sets]
        exact = [c for c in same_set if normalize_number(c.number) == nnum and rarity_class(c.rarity, "en") == rcls]
        if exact:
            c = exact[0]
            return Resolution("identical", c, 0.95, canonical_key(c.name, c.set_code, c.number, rcls))
        by_rarity = [c for c in same_set if rarity_class(c.rarity, "en") == rcls]
        if by_rarity:
            c = by_rarity[0]
            return Resolution("identical", c, 0.75, canonical_key(c.name, c.set_code, c.number, rcls),
                              "matched on set+rarity; numbering differs (JP sets number by set order)")

    # 3) promo exclusivity: pick the closest EN print of the same name as a proxy
    if is_promo_set(ja.set_code):
        proxy = _closest(ja, en_pool)
        if proxy:
            return Resolution("promo_exclusive", proxy, 0.5,
                              canonical_key(ja.name, proxy.set_code, proxy.number, rcls),
                              f"JP promo; EN proxy {proxy.set_code} #{proxy.number}")
        return Resolution("no_equivalent", None, 1.0, canonical_key(ja.name, None, ja.number, rcls), "JP promo, no EN print")

    # 4) any EN print with same name + rarity class (reprint)
    reprint = [c for c in en_pool if rarity_class(c.rarity, "en") == rcls]
    if reprint:
        c = _closest(ja, reprint) or reprint[0]
        return Resolution("reprint", c, 0.6, canonical_key(c.name, c.set_code, c.number, rcls))
    return Resolution("no_equivalent", None, 0.8, canonical_key(ja.name, None, ja.number, rcls))


def _closest(ja: CardSpec, pool: list[CardSpec]) -> CardSpec | None:
    if not pool:
        return None
    if ja.artist:
        same_artist = [c for c in pool if c.artist and c.artist.lower() == ja.artist.lower()]
        if same_artist:
            pool = same_artist
    if ja.release_date:
        dated = [c for c in pool if c.release_date]
        if dated:
            return min(dated, key=lambda c: abs(_days(c.release_date) - _days(ja.release_date)))
    return pool[0]


def _days(iso: str | None) -> int:
    from datetime import date
    return date.fromisoformat(iso).toordinal() if iso else 0
