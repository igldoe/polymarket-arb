"""Polymarket: Gamma API (рынки и правила) + CLOB API (стаканы)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from ..http import ApiClient
from ..models import Level, Market, OrderBook
from .base import VenueAdapter, parse_iso

log = logging.getLogger(__name__)


def _json_list(value: Any) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return value or []


def parse_market(raw: dict) -> Market | None:
    outcomes = _json_list(raw.get("outcomes"))
    tokens = _json_list(raw.get("clobTokenIds"))
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    if not raw.get("enableOrderBook") or not raw.get("acceptingOrders", True):
        return None
    events = raw.get("events") or [{}]
    event = events[0]
    fee = raw.get("feeSchedule") or {}
    question = raw.get("question") or ""
    if [o.lower() for o in outcomes] != ["yes", "no"]:
        # Рынок с именованными исходами ("49ers vs. Seahawks" → ["49ers", "Seahawks"]):
        # сторона YES = первый исход, NO = второй.
        question = f"{question} — will {outcomes[0]} win (vs {outcomes[1]})?"
    best_bid, best_ask = raw.get("bestBid"), raw.get("bestAsk")
    return Market(
        venue="polymarket",
        id=str(raw["id"]),
        question=question,
        rules=raw.get("description") or event.get("description") or "",
        close_time=parse_iso(raw.get("endDate")),
        resolution_source=raw.get("resolutionSource") or event.get("resolutionSource") or "",
        outcomes=list(outcomes),
        url=f"https://polymarket.com/event/{event.get('slug') or raw.get('slug')}",
        category=raw.get("feeType") or raw.get("category") or "",
        extra={
            "yes_token": tokens[0],
            "no_token": tokens[1],
            "event_title": event.get("title", ""),
            "group_item": raw.get("groupItemTitle") or "",
            "fees_enabled": bool(raw.get("feesEnabled", True)),
            "fee_rate": fee.get("rate"),
            "fee_exponent": fee.get("exponent", 1),
            "neg_risk": bool(raw.get("negRisk")),
            "tick": raw.get("orderPriceMinTickSize"),
            "min_size": raw.get("orderMinSize"),
            "volume_24h": raw.get("volume24hr") or 0,
            # Ориентировочный верх стакана из Gamma — для дешёвого ценового фильтра до LLM.
            "yes_ask": float(best_ask) if best_ask else None,
            "no_ask": round(1 - float(best_bid), 6) if best_bid else None,
        },
    )


def parse_side(raw_book: dict) -> list[Level]:
    """Аски CLOB-стакана одного токена, по возрастанию цены."""
    levels = [Level(float(a["price"]), float(a["size"])) for a in raw_book.get("asks") or []]
    return sorted((l for l in levels if l.size > 0), key=lambda l: l.price)


class PolymarketAdapter(VenueAdapter):
    name = "polymarket"

    def __init__(self, gamma_url: str, clob_url: str, rate_per_sec: float = 20):
        self.gamma = ApiClient(gamma_url, rate=rate_per_sec, burst=int(rate_per_sec), concurrency=24)
        self.clob = ApiClient(clob_url, rate=rate_per_sec, burst=int(rate_per_sec))

    async def fetch_markets(self, min_close: datetime, max_close: datetime) -> list[Market]:
        # Keyset-пагинация последовательна, поэтому окно дат режем на отрезки
        # и листаем их параллельно (рынков >100 тыс., в основном краткосрочные).
        bounds = [min_close]
        while bounds[-1] < max_close:
            # Ближайшие дни плотные (спорт, крипто-раунды) — режем мельче.
            step = timedelta(hours=3) if bounds[-1] - min_close < timedelta(days=14) else timedelta(days=2)
            bounds.append(min(bounds[-1] + step, max_close))
        chunks = await asyncio.gather(*(self._fetch_range(a, b) for a, b in zip(bounds, bounds[1:])))
        unique = {m.id: m for chunk in chunks for m in chunk}
        log.info("polymarket: %d бинарных рынков", len(unique))
        return list(unique.values())

    async def _fetch_range(self, start: datetime, end: datetime) -> list[Market]:
        markets: list[Market] = []
        cursor = None
        while True:
            data = await self.gamma.get(
                "/markets/keyset", active="true", closed="false", limit=100,
                end_date_min=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_date_max=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                after_cursor=cursor,
            )
            markets.extend(m for raw in data.get("markets") or [] if (m := parse_market(raw)))
            cursor = data.get("next_cursor")
            if not cursor or not data.get("markets"):
                return markets

    async def fetch_book(self, market: Market) -> OrderBook:
        yes_id, no_id = market.extra["yes_token"], market.extra["no_token"]
        books = await self.clob.post("/books", json=[{"token_id": yes_id}, {"token_id": no_id}])
        by_token = {b.get("asset_id"): b for b in books}
        return OrderBook(
            yes_asks=parse_side(by_token.get(yes_id, {})),
            no_asks=parse_side(by_token.get(no_id, {})),
        )

    async def aclose(self) -> None:
        await self.gamma.aclose()
        await self.clob.aclose()
