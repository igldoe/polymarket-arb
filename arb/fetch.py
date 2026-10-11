"""Загрузка рынков со всех площадок в SQLite: `python -m arb.fetch [--books N]`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from .adapters import VenueAdapter, build_adapters
from .config import ROOT, Config, load_config
from .db import Database
from .models import Market

log = logging.getLogger("arb.fetch")


def close_window(cfg: Config) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return (now + timedelta(hours=cfg.get("scan.min_hours_to_close", 1)),
            now + timedelta(days=cfg.get("scan.max_days_to_close", 120)))


async def refresh_venue(adapter: VenueAdapter, db: Database, cfg: Config) -> list[Market]:
    t0 = time.monotonic()
    markets = await adapter.fetch_markets(*close_window(cfg))
    await db.upsert_markets(adapter.name, markets)
    log.info("%s: сохранено %d рынков за %.1fs", adapter.name, len(markets), time.monotonic() - t0)
    return markets


async def main(sample_books: int) -> None:
    cfg = load_config()
    db = await Database(ROOT / cfg.get("database")).connect()
    adapters = build_adapters(cfg)
    try:
        results = await asyncio.gather(
            *(refresh_venue(a, db, cfg) for a in adapters.values()), return_exceptions=True)
        for adapter, res in zip(adapters.values(), results):
            if isinstance(res, BaseException):
                log.error("%s: ошибка загрузки: %s", adapter.name, res)
                continue
            # Пример: самые близкие по закрытию рынки и их стаканы.
            for m in sorted(res, key=lambda m: m.close_time)[:sample_books]:
                book = await adapter.fetch_book(m)
                ya = book.yes_asks[0] if book.yes_asks else None
                na = book.no_asks[0] if book.no_asks else None
                print(f"  [{adapter.name}] {m.question[:70]!r} закрытие {m.close_time:%Y-%m-%d %H:%M}"
                      f" | YES ask {ya.price if ya else '-'} x {ya.size if ya else 0:.0f}"
                      f" | NO ask {na.price if na else '-'} x {na.size if na else 0:.0f}")
        print("Активных рынков в БД:", await db.market_counts())
    finally:
        for a in adapters.values():
            await a.aclose()
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--books", type=int, default=3, help="сколько стаканов показать на площадку")
    asyncio.run(main(p.parse_args().books))
