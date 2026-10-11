"""Kalshi: публичные эндпоинты Trade API v2 (без авторизации)."""
from __future__ import annotations

import logging
from datetime import datetime

from ..http import ApiClient
from ..models import Level, Market, OrderBook
from .base import VenueAdapter, parse_iso

log = logging.getLogger(__name__)

# Комбо-рынки (парлеи) — сопоставлять их не с чем.
SKIP_PREFIXES = ("KXMVE",)


def parse_event(event: dict, series: dict[str, dict]) -> list[Market]:
    if event.get("event_ticker", "").startswith(SKIP_PREFIXES):
        return []
    sources = ", ".join(
        f"{s.get('name', '').strip()} ({s.get('url', '')})"
        for s in event.get("settlement_sources") or [] if s.get("name", "").strip()
    )
    s = series.get(event.get("series_ticker", ""), {})
    out = []
    for m in event.get("markets") or []:
        if m.get("market_type") != "binary" or m.get("status") not in ("active", "open"):
            continue
        title = m.get("title") or event.get("title", "")
        sub = m.get("yes_sub_title") or ""
        question = title if not sub or sub.lower() in title.lower() else f"{title} — {sub}"
        rules = "\n".join(filter(None, [m.get("rules_primary"), m.get("rules_secondary")]))
        out.append(Market(
            venue="kalshi",
            id=m["ticker"],
            question=question,
            rules=rules,
            close_time=parse_iso(m.get("close_time")),
            resolution_source=sources,
            outcomes=["Yes", "No"],
            url=f"https://kalshi.com/markets/{event.get('series_ticker', '').lower()}",
            category=event.get("category", ""),
            extra={
                "event_ticker": event.get("event_ticker"),
                "event_title": event.get("title", ""),
                "series_ticker": event.get("series_ticker"),
                "expected_expiration": m.get("expected_expiration_time"),
                "can_close_early": m.get("can_close_early"),
                "fee_type": s.get("fee_type", "quadratic"),
                "fee_multiplier": s.get("fee_multiplier", 1),
                "volume_24h": float(m.get("volume_24h_fp") or 0),
            },
        ))
    return out


def parse_book(raw: dict) -> OrderBook:
    """Kalshi отдаёт только биды. Купить YES по p = продать NO по 1-p, поэтому
    аски YES — это биды NO, отражённые через 1, и наоборот."""
    ob = raw.get("orderbook_fp") or {}

    def flip(bids: list | None) -> list[Level]:
        levels = [Level(round(1 - float(p), 6), float(s)) for p, s in bids or []]
        return sorted((l for l in levels if l.size > 0), key=lambda l: l.price)

    return OrderBook(yes_asks=flip(ob.get("no_dollars")), no_asks=flip(ob.get("yes_dollars")))


class KalshiAdapter(VenueAdapter):
    name = "kalshi"

    def __init__(self, base_url: str, rate_per_sec: float = 8):
        self.api = ApiClient(base_url, rate=rate_per_sec, burst=int(rate_per_sec) * 2)

    async def fetch_markets(self, min_close: datetime, max_close: datetime) -> list[Market]:
        series_list = (await self.api.get("/series")).get("series") or []
        series = {s["ticker"]: s for s in series_list}
        markets: list[Market] = []
        cursor = None
        while True:
            data = await self.api.get("/events", status="open", limit=200,
                                      with_nested_markets="true", cursor=cursor)
            for event in data.get("events") or []:
                for m in parse_event(event, series):
                    if m.close_time and min_close <= m.close_time <= max_close:
                        markets.append(m)
            cursor = data.get("cursor")
            if not cursor or not data.get("events"):
                break
        log.info("kalshi: %d бинарных рынков", len(markets))
        return markets

    async def fetch_book(self, market: Market) -> OrderBook:
        return parse_book(await self.api.get(f"/markets/{market.id}/orderbook"))

    async def aclose(self) -> None:
        await self.api.aclose()
