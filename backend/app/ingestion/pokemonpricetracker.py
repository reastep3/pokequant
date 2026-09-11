"""PokemonPriceTracker oracle — raw (TCGplayer market) and graded (eBay PSA/BGS/CGC) prices.

Assumed v2 shape (verify against your plan's API docs; the extractor is path-tolerant):

    GET {base}/cards?id=<ppt card id>            (Bearer auth)
    -> {"data": [{
          "id": "...", "name": "Charizard", "setId": "base1", "number": "4",
          "prices": {
             "market": 412.5, "low": 380, "high": 470,           # raw / TCGplayer
             "tcgplayer": {"market": 412.5, "listings": 57},
             "ebay": {"psa10": {"market": 4100, "sales": 12, "low": 3800, "high": 4500},
                      "psa9": {...}, "bgs9.5": {...}, "cgc10": {...}}
          },
          "priceHistory": [{"date": "2026-09-01", "market": 400.0}, ...]
    }]}

Each request yields one RAW tick plus one tick per graded key present.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.ingestion.base import AssetRef, OracleClient, PricePoint, SaleRecord, dig, parse_ts

log = logging.getLogger(__name__)

GRADED_KEYS = {"psa10": "PSA10", "psa9": "PSA9", "psa8": "PSA8", "bgs10": "BGS10", "bgs9.5": "BGS9.5",
               "bgs95": "BGS9.5", "cgc10": "CGC10", "cgc9.5": "CGC9.5", "cgc95": "CGC9.5"}


class PokemonPriceTrackerClient:
    name = "ppt"
    CARDS_PATH = "/cards"
    EXTERNAL_KEY = "ppt"

    def __init__(self, base_url: str, api_key: str | None, *, rps: float = 2.0, timeout: float = 20.0):
        self.http = OracleClient(base_url, api_key, rps=rps, timeout=timeout)

    def _extract(self, ref: AssetRef, card: dict, ts: datetime) -> list[PricePoint]:
        out: list[PricePoint] = []
        prices = dig(card, "prices", default={}) or {}
        raw = dig(prices, "market", "tcgplayer.market", "tcgplayer.marketPrice", "raw.market")
        if raw is not None:
            out.append(PricePoint(asset_id=ref.asset_id, grade_key="RAW", ts=ts, price=float(raw), source=self.name,
                                  low=dig(prices, "low", "tcgplayer.low"), high=dig(prices, "high", "tcgplayer.high"),
                                  n_listings=dig(prices, "tcgplayer.listings", "listings"),
                                  volume=dig(prices, "tcgplayer.sales", "sales")))
        graded = dig(prices, "ebay", "graded", default={}) or {}
        for k, v in graded.items():
            gk = GRADED_KEYS.get(str(k).lower().replace("_", ""))
            if not gk or not isinstance(v, dict):
                continue
            px = dig(v, "market", "price", "avg", "median")
            if px is None:
                continue
            out.append(PricePoint(asset_id=ref.asset_id, grade_key=gk, ts=ts, price=float(px), source=self.name,
                                  low=dig(v, "low", "min"), high=dig(v, "high", "max"),
                                  volume=dig(v, "sales", "count", "n"), n_listings=dig(v, "listings")))
        return out

    async def fetch_prices(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[PricePoint]:
        out: list[PricePoint] = []
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        for ref in refs:
            ext = ref.external_ids.get(self.EXTERNAL_KEY)
            if not ext:
                continue
            try:
                data = await self.http.get_json(self.CARDS_PATH, {"id": ext})
            except Exception as e:  # noqa: BLE001
                log.warning("ppt fetch failed for %s: %s", ref.ticker, e)
                continue
            cards = dig(data, "data", default=data)
            card = cards[0] if isinstance(cards, list) and cards else cards
            if not isinstance(card, dict):
                continue
            out.extend(self._extract(ref, card, now))
            # Backfill from embedded history when the oracle provides it (raw only).
            for h in dig(card, "priceHistory", "history", default=[]) or []:
                d, px = dig(h, "date", "ts"), dig(h, "market", "price")
                if d is None or px is None:
                    continue
                ts = parse_ts(d)
                if since and ts < since:
                    continue
                out.append(PricePoint(asset_id=ref.asset_id, grade_key="RAW", ts=ts, price=float(px), source=self.name))
        return out

    async def fetch_sales(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[SaleRecord]:
        return []  # PPT publishes aggregates, not lots

    async def aclose(self) -> None:
        await self.http.aclose()
