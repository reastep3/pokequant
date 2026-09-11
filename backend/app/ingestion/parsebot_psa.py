"""Parse.bot PSA oracle — population reports + PSA auction-price realizations.

Parse.bot exposes *user-defined* scraper APIs, so endpoint paths and field names are
configurable rather than hard-coded. The defaults below assume an API scraping
psacard.com/pop and psacard.com/auctionprices with this shape:

    GET {base}/psa/pop?spec_id=<PSA spec id>
      -> {"spec_id": "...", "as_of": "2026-09-10", "grades": [{"grade": "10", "count": 1234, "qualifier_count": 3}, ...]}
    GET {base}/psa/auction-prices?spec_id=<id>&grade=10&since=YYYY-MM-DD
      -> {"sales": [{"date": "...", "price": 1234.5, "cert": "12345678", "lot_id": "...", "venue": "eBay"}]}

Adjust ``POP_PATH`` / ``SALES_PATH`` / ``FIELDS`` (via subclassing or env) to match your Parse.bot schema.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.ingestion.base import (
    AssetRef,
    OracleClient,
    PopSnapshot,
    PricePoint,
    SaleRecord,
    dig,
    parse_ts,
)

log = logging.getLogger(__name__)

GRADE_NORMALIZE = {"GEM MT 10": "10", "GEM-MT 10": "10", "MINT 9": "9", "NM-MT 8": "8", "NM 7": "7",
                   "EX-MT 6": "6", "EX 5": "5", "VG-EX 4": "4", "VG 3": "3", "GOOD 2": "2", "PR 1": "1"}


def normalize_grade_label(label: str) -> str | None:
    s = str(label).strip().upper()
    if s in GRADE_NORMALIZE:
        return GRADE_NORMALIZE[s]
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    return digits or None


class ParseBotPSAClient:
    name = "parsebot"
    POP_PATH = "/psa/pop"
    SALES_PATH = "/psa/auction-prices"
    EXTERNAL_KEY = "psa_spec"

    def __init__(self, base_url: str, api_key: str | None, *, rps: float = 2.0, timeout: float = 20.0):
        self.http = OracleClient(base_url, api_key, rps=rps, timeout=timeout, auth_header="X-API-Key", auth_prefix="")

    async def fetch_pop(self, refs: Sequence[AssetRef]) -> list[PopSnapshot]:
        out: list[PopSnapshot] = []
        for ref in refs:
            spec = ref.external_ids.get(self.EXTERNAL_KEY)
            if not spec:
                continue
            try:
                data = await self.http.get_json(self.POP_PATH, {"spec_id": spec})
            except Exception as e:  # noqa: BLE001 - one bad spec must not abort the batch
                log.warning("pop fetch failed for %s: %s", ref.ticker, e)
                continue
            counts: dict[str, int] = {}
            for row in dig(data, "grades", "data.grades", "pop", default=[]) or []:
                g = normalize_grade_label(dig(row, "grade", "label", default=""))
                n = dig(row, "count", "population", "total", default=0)
                if g:
                    counts[g] = counts.get(g, 0) + int(n or 0)
            if not counts:
                continue
            ts = parse_ts(dig(data, "as_of", "updated_at", "date", default=datetime.now(UTC)))
            out.append(PopSnapshot(asset_id=ref.asset_id, grader="PSA", ts=ts, grade_counts=counts,
                                   total=sum(counts.values()), source=self.name,
                                   meta={"spec_id": spec, "qualifiers": dig(data, "qualifiers", default=None)}))
        return out

    async def fetch_sales(self, refs: Sequence[AssetRef], since: datetime | None = None,
                          grades: Sequence[str] = ("10", "9", "8")) -> list[SaleRecord]:
        out: list[SaleRecord] = []
        for ref in refs:
            spec = ref.external_ids.get(self.EXTERNAL_KEY)
            if not spec:
                continue
            for g in grades:
                params = {"spec_id": spec, "grade": g}
                if since:
                    params["since"] = since.date().isoformat()
                try:
                    data = await self.http.get_json(self.SALES_PATH, params)
                except Exception as e:  # noqa: BLE001
                    log.warning("sales fetch failed for %s PSA%s: %s", ref.ticker, g, e)
                    continue
                for s in dig(data, "sales", "data.sales", "results", default=[]) or []:
                    price = dig(s, "price", "sale_price", "amount")
                    date = dig(s, "date", "sale_date", "sold_at")
                    if price is None or date is None:
                        continue
                    out.append(SaleRecord(asset_id=ref.asset_id, grade_key=f"PSA{g}", ts=parse_ts(date),
                                          price=float(price), source=self.name,
                                          external_id=str(dig(s, "lot_id", "id", "url", default="")) or None,
                                          cert_number=str(dig(s, "cert", "cert_number", default="")) or None,
                                          meta={"venue": dig(s, "venue", "auction_house")}))
        return out

    async def fetch_prices(self, refs: Sequence[AssetRef], since: datetime | None = None) -> list[PricePoint]:
        """Parse.bot PSA is a sales oracle; daily bars are aggregated downstream (pipeline.aggregate_sales)."""
        return []

    async def aclose(self) -> None:
        await self.http.aclose()
