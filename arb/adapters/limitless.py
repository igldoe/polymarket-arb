"""Limitless Exchange: публичный REST API."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..http import ApiClient
from ..models import Level, Market, OrderBook
from .base import VenueAdapter, strip_html

log = logging.getLogger(__name__)

SIZE_SCALE = 1_000_000  # размеры в стакане — сырые единицы токена с 6 знаками


def _close(raw: dict) -> datetime | None:
    ts = raw.get("expirationTimestamp")
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None


def _market(raw: dict, group: dict | None = None) -> Market | None:
    if raw.get("tradeType") != "clob" or raw.get("status") not in ("FUNDED", "CREATED"):
        return None
    if raw.get("expired") or not raw.get("tokens"):
        return None
    title = raw.get("title", "")
    question = f"{group['title']} — {title}" if group else title
    meta = raw.get("metadata") or {}
    buy = ((raw.get("tradePrices") or {}).get("buy") or {}).get("market") or [None, None]
    return Market(
        venue="limitless",
        id=raw["slug"],
        question=question,
        rules=strip_html(raw.get("description") or (group or {}).get("description")),
        close_time=_close(raw) or (_close(group) if group else None),
        resolution_source="",
        outcomes=["Yes", "No"],
        url=f"https://limitless.exchange/markets/{(group or raw)['slug']}",
        category=", ".join(raw.get("categories") or (group or {}).get("categories") or []),
        extra={
            "group_title": group["title"] if group else "",
            "fees_enabled": bool(meta.get("fee", True)),
            "external_provider": meta.get("externalProvider"),
            "yes_token": raw["tokens"].get("yes"),
            "no_token": raw["tokens"].get("no"),
            "yes_ask": buy[0] if buy[0] and 0 < buy[0] < 1 else None,
            "no_ask": buy[1] if buy[1] and 0 < buy[1] < 1 else None,
        },
    )


def parse_markets(raw_list: list[dict]) -> list[Market]:
    out = []
    for raw in raw_list:
        if raw.get("marketType") == "group":
            out.extend(m for sub in raw.get("markets") or [] if (m := _market(sub, raw)))
        elif m := _market(raw):
            out.append(m)
    return out


def parse_book(raw: dict) -> OrderBook:
    """Стакан только по стороне YES: аски YES как есть, аски NO = 1 - биды YES."""
    yes = [Level(float(a["price"]), float(a["size"]) / SIZE_SCALE) for a in raw.get("asks") or []]
    no = [Level(round(1 - float(b["price"]), 6), float(b["size"]) / SIZE_SCALE)
          for b in raw.get("bids") or []]
    key = lambda l: l.price  # noqa: E731
    return OrderBook(yes_asks=sorted((l for l in yes if l.size > 0), key=key),
                     no_asks=sorted((l for l in no if l.size > 0), key=key))


class LimitlessAdapter(VenueAdapter):
    name = "limitless"
    PAGE = 25  # максимум, который принимает API

    def __init__(self, base_url: str, rate_per_sec: float = 1):
        self.api = ApiClient(base_url, rate=rate_per_sec, burst=3, concurrency=2)

    async def fetch_markets(self, min_close: datetime, max_close: datetime) -> list[Market]:
        markets: list[Market] = []
        page = 1
        while True:
            data = await self.api.get("/markets/active", limit=self.PAGE, page=page)
            batch = data.get("data") or []
            for m in parse_markets(batch):
                if m.close_time and min_close <= m.close_time <= max_close:
                    markets.append(m)
            total = data.get("totalMarketsCount") or 0
            if not batch or page * self.PAGE >= total:
                break
            page += 1
        log.info("limitless: %d бинарных рынков", len(markets))
        return markets

    async def fetch_book(self, market: Market) -> OrderBook:
        return parse_book(await self.api.get(f"/markets/{market.id}/orderbook"))

    async def aclose(self) -> None:
        await self.api.aclose()
