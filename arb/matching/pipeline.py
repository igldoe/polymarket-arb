"""Сопоставление рынков: предфильтр → кэш → проверка через Claude → SQLite."""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field

from ..config import Config
from ..db import Database
from ..models import Market
from .llm import LLMVerifier, rules_hash
from .prefilter import Candidate, Prefilter, quick_gross_edge

log = logging.getLogger(__name__)


@dataclass
class MatchStats:
    candidates: int = 0
    cached: int = 0
    queued: int = 0
    checked: int = 0
    confirmed: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    verdicts: list[tuple[Candidate, dict]] = field(default_factory=list)


def orient(c: Candidate) -> Candidate:
    """Порядок рынков в паре как в ключе БД (a_key < b_key)."""
    return c if c.a.key < c.b.key else Candidate(c.b, c.a, c.score, c.reasons)


def build_prefilter(cfg: Config) -> Prefilter:
    return Prefilter(
        close_tolerance_days=cfg.get("matching.close_tolerance_days", 7),
        min_score=cfg.get("matching.min_fuzzy_score", 70),
        top_k=cfg.get("matching.top_k", 3),
    )


async def run_matching(db: Database, cfg: Config, markets: dict[str, list[Market]],
                       verifier: LLMVerifier | None, max_checks: int | None = None) -> MatchStats:
    stats = MatchStats()
    prefilter = build_prefilter(cfg)
    cands: list[Candidate] = []
    for va, vb in itertools.combinations(sorted(markets), 2):
        cands.extend(orient(c) for c in await asyncio.to_thread(prefilter.find, markets[va], markets[vb]))
    stats.candidates = len(cands)
    await db.upsert_candidates([(c.a.key, c.b.key, c.score, c.reasons) for c in cands])

    known = await db.get_pairs()
    min_edge = cfg.get("matching.llm_min_gross_edge", 0.0)
    queue: list[tuple[float, Candidate, str]] = []
    for c in cands:
        row = known.get(c.key, {})
        h = rules_hash(c.a, c.b)
        if row.get("manual") or (row.get("checked_at") and row.get("rules_hash") == h):
            stats.cached += 1
            continue
        edge = quick_gross_edge(c)
        if edge is None or edge <= min_edge:
            continue
        queue.append((edge, c, h))
    queue.sort(key=lambda x: x[0], reverse=True)   # сначала самые перспективные по цене
    limit = max_checks if max_checks is not None else cfg.get("matching.max_llm_checks_per_cycle", 100)
    stats.queued = len(queue)
    if verifier is None or limit <= 0:
        return stats

    sem = asyncio.Semaphore(cfg.get("matching.llm_concurrency", 4))

    async def check(c: Candidate, h: str) -> None:
        async with sem:
            try:
                res = await verifier.verify(c.a, c.b)
            except Exception as e:  # noqa: BLE001 — одна пара не должна ронять цикл
                stats.errors += 1
                log.warning("LLM ошибка для %s ↔ %s: %s", c.a.key, c.b.key, e)
                return
        verdict = res.verdict.model_dump()
        await db.save_verdict(c.a.key, c.b.key, h, verdict, res.model)
        stats.checked += 1
        stats.input_tokens += res.input_tokens
        stats.output_tokens += res.output_tokens
        stats.verdicts.append((c, verdict))
        if verdict["same_event"] and verdict["confidence"] >= cfg.get("matching.min_confidence", 0.9):
            stats.confirmed += 1

    await asyncio.gather(*(check(c, h) for _, c, h in queue[:limit]))
    return stats
