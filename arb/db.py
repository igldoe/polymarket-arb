"""SQLite-хранилище: рынки площадок (пары и алерты добавятся на следующих этапах)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .adapters.base import parse_iso
from .models import Market

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    venue TEXT NOT NULL,
    id TEXT NOT NULL,
    question TEXT NOT NULL,
    rules TEXT NOT NULL,
    close_time TEXT,
    resolution_source TEXT,
    outcomes TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT,
    extra TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (venue, id)
);
CREATE INDEX IF NOT EXISTS idx_markets_close ON markets(close_time);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> "Database":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        return self

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def upsert_markets(self, venue: str, markets: list[Market]) -> None:
        """Сохраняет актуальный список рынков площадки; пропавшие помечаются неактивными."""
        assert self.conn
        now = _now()
        await self.conn.executemany(
            """INSERT INTO markets (venue, id, question, rules, close_time, resolution_source,
                                    outcomes, url, category, extra, active, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(venue, id) DO UPDATE SET
                 question=excluded.question, rules=excluded.rules, close_time=excluded.close_time,
                 resolution_source=excluded.resolution_source, outcomes=excluded.outcomes,
                 url=excluded.url, category=excluded.category, extra=excluded.extra,
                 active=1, updated_at=excluded.updated_at""",
            [(m.venue, m.id, m.question, m.rules,
              m.close_time.isoformat() if m.close_time else None, m.resolution_source,
              json.dumps(m.outcomes), m.url, m.category, json.dumps(m.extra), now)
             for m in markets],
        )
        await self.conn.execute(
            "UPDATE markets SET active=0 WHERE venue=? AND updated_at<>?", (venue, now))
        await self.conn.commit()

    async def load_markets(self, venue: str | None = None) -> list[Market]:
        assert self.conn
        sql = "SELECT * FROM markets WHERE active=1"
        args: tuple = ()
        if venue:
            sql += " AND venue=?"
            args = (venue,)
        async with self.conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [Market(
            venue=r["venue"], id=r["id"], question=r["question"], rules=r["rules"],
            close_time=parse_iso(r["close_time"]), resolution_source=r["resolution_source"] or "",
            outcomes=json.loads(r["outcomes"]), url=r["url"], category=r["category"] or "",
            extra=json.loads(r["extra"]),
        ) for r in rows]

    async def market_counts(self) -> dict[str, int]:
        assert self.conn
        async with self.conn.execute(
                "SELECT venue, COUNT(*) FROM markets WHERE active=1 GROUP BY venue") as cur:
            return {v: n for v, n in await cur.fetchall()}
