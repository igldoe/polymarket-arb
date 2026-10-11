"""Единые структуры данных для всех площадок."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Level:
    """Уровень стакана: цена за шейр ($, 0..1) и объём в шейрах (1 шейр выплачивает $1)."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Нормализованный стакан бинарного рынка: аски на покупку YES и на покупку NO.

    Аски отсортированы по возрастанию цены. Для площадок, где отдаются только биды
    одной стороны, аски противоположной стороны получаются как 1 - bid.
    """
    yes_asks: list[Level] = field(default_factory=list)
    no_asks: list[Level] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def asks(self, side: str) -> list[Level]:
        return self.yes_asks if side == "YES" else self.no_asks


@dataclass
class Market:
    venue: str                       # "polymarket" | "kalshi" | "limitless"
    id: str                          # уникальный id на площадке
    question: str
    rules: str                       # полный текст правил разрешения
    close_time: datetime | None      # когда рынок перестаёт торговаться / ожидаемое разрешение
    resolution_source: str
    outcomes: list[str]
    url: str
    category: str = ""
    # Параметры площадки: токены, параметры комиссии и т.п. Сериализуются в SQLite как JSON.
    extra: dict[str, Any] = field(default_factory=dict)
    book: OrderBook | None = None

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.id}"

    def days_to_close(self, now: datetime | None = None) -> float | None:
        if self.close_time is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds() / 86400
