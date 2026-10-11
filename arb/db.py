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

-- Пары рынков разных площадок: кандидаты предфильтра, кэш вердиктов LLM и ручные пометки.
CREATE TABLE IF NOT EXISTS pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    a_key TEXT NOT NULL,              -- "venue:id", a_key < b_key
    b_key TEXT NOT NULL,
    score REAL,                       -- fuzzy-сходство предфильтра
    reasons TEXT,
    rules_hash TEXT,                  -- хэш текстов, по которым вынесен вердикт
    same_event INTEGER,
    yes_equivalent INTEGER,           -- 1: YES_a ≡ YES_b, 0: YES_a ≡ NO_b
    confidence REAL,
    resolution_risk TEXT,
    differences TEXT,
    checks TEXT,
    llm_model TEXT,
    checked_at TEXT,
    manual TEXT,                      -- NULL | 'confirmed' | 'rejected'
    manual_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (a_key, b_key)
);
"""

PAIR_FIELDS = ("id", "a_key", "b_key", "score", "reasons", "rules_hash", "same_event",
               "yes_equivalent", "confidence", "resolution_risk", "differences", "checks",
               "llm_model", "checked_at", "manual", "manual_at")


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

    # --- пары -------------------------------------------------------------

    async def upsert_candidates(self, rows: list[tuple[str, str, float, list[str]]]) -> None:
        """rows: (a_key, b_key, score, reasons), a_key < b_key. Вердикты не трогаются."""
        assert self.conn
        now = _now()
        await self.conn.executemany(
            """INSERT INTO pairs (a_key, b_key, score, reasons, created_at, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(a_key, b_key) DO UPDATE SET
                 score=excluded.score, reasons=excluded.reasons, updated_at=excluded.updated_at""",
            [(a, b, sc, json.dumps(r, ensure_ascii=False), now, now) for a, b, sc, r in rows])
        await self.conn.commit()

    async def get_pairs(self, keys: list[tuple[str, str]] | None = None) -> dict[tuple[str, str], dict]:
        assert self.conn
        async with self.conn.execute(f"SELECT {', '.join(PAIR_FIELDS)} FROM pairs") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        out = {(r["a_key"], r["b_key"]): _decode_pair(r) for r in rows}
        if keys is not None:
            wanted = set(keys)
            out = {k: v for k, v in out.items() if k in wanted}
        return out

    async def get_pair_by_id(self, pair_id: int) -> dict | None:
        assert self.conn
        async with self.conn.execute(
                f"SELECT {', '.join(PAIR_FIELDS)} FROM pairs WHERE id=?", (pair_id,)) as cur:
            row = await cur.fetchone()
        return _decode_pair(dict(row)) if row else None

    async def save_verdict(self, a_key: str, b_key: str, rules_hash: str, verdict: dict,
                           model: str) -> None:
        assert self.conn
        now = _now()
        await self.conn.execute(
            """UPDATE pairs SET rules_hash=?, same_event=?, yes_equivalent=?, confidence=?,
                 resolution_risk=?, differences=?, checks=?, llm_model=?, checked_at=?, updated_at=?
               WHERE a_key=? AND b_key=?""",
            (rules_hash, int(verdict["same_event"]), int(verdict["yes_equivalent"]),
             verdict["confidence"], verdict["resolution_risk"],
             json.dumps(verdict["differences"], ensure_ascii=False),
             json.dumps(verdict["checks"], ensure_ascii=False), model, now, now, a_key, b_key))
        await self.conn.commit()

    async def set_manual(self, pair_id: int, status: str | None) -> bool:
        """Ручная пометка пары: 'confirmed', 'rejected' или None (снять пометку)."""
        assert self.conn and status in ("confirmed", "rejected", None)
        cur = await self.conn.execute(
            "UPDATE pairs SET manual=?, manual_at=? WHERE id=?",
            (status, _now() if status else None, pair_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def matched_pairs(self, min_confidence: float) -> list[dict]:
        """Пары для поиска вилок: подтверждённые вручную или LLM с достаточной уверенностью."""
        assert self.conn
        async with self.conn.execute(
                f"""SELECT {', '.join(PAIR_FIELDS)} FROM pairs
                    WHERE manual='confirmed'
                       OR (manual IS NULL AND same_event=1 AND confidence>=?)""",
                (min_confidence,)) as cur:
            return [_decode_pair(dict(r)) for r in await cur.fetchall()]

    async def pair_stats(self) -> dict[str, int]:
        assert self.conn
        async with self.conn.execute(
                """SELECT COUNT(*), SUM(checked_at IS NOT NULL), SUM(same_event=1),
                          SUM(manual='confirmed'), SUM(manual='rejected') FROM pairs""") as cur:
            total, checked, same, conf, rej = await cur.fetchone()
        return {"candidates": total or 0, "llm_checked": checked or 0, "llm_same": same or 0,
                "manual_confirmed": conf or 0, "manual_rejected": rej or 0}


def _decode_pair(r: dict) -> dict:
    for k in ("reasons", "differences", "checks"):
        r[k] = json.loads(r[k]) if r.get(k) else ([] if k != "checks" else {})
    return r
