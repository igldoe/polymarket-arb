"""Единый интерфейс адаптера площадки. Новая площадка = новый подкласс VenueAdapter."""
from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..models import Market, OrderBook


class VenueAdapter(ABC):
    name: str

    @abstractmethod
    async def fetch_markets(self, min_close: datetime, max_close: datetime) -> list[Market]:
        """Все открытые бинарные рынки со стаканом, закрывающиеся в окне [min_close, max_close]."""

    @abstractmethod
    async def fetch_book(self, market: Market) -> OrderBook:
        """Текущий стакан рынка, нормализованный к аскам YES и NO."""

    async def aclose(self) -> None:
        pass


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"</p>|<br\s*/?>", "\n", text)
    text = html.unescape(_TAG_RE.sub("", text))
    return re.sub(r"\n{2,}", "\n", text).strip()
