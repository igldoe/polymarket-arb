"""Сопоставление рынков из БД: `python -m arb.match [--llm N]`.

Без ANTHROPIC_API_KEY выполняется только предфильтр."""
from __future__ import annotations

import argparse
import asyncio
import logging

from .config import ROOT, Config, load_config
from .db import Database
from .matching.llm import LLMVerifier
from .matching.pipeline import run_matching
from .matching.prefilter import quick_gross_edge

log = logging.getLogger("arb.match")


def build_verifier(cfg: Config) -> LLMVerifier | None:
    if not cfg.env("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY не задан — проверка через Claude пропущена")
        return None
    return LLMVerifier(model=cfg.env("ANTHROPIC_MODEL", "claude-opus-5-5"),
                       effort=cfg.env("ANTHROPIC_EFFORT", "medium"))


async def main(max_checks: int | None) -> None:
    cfg = load_config()
    db = await Database(ROOT / cfg.get("database")).connect()
    try:
        markets = {}
        for venue in cfg.get("venues"):
            markets[venue] = await db.load_markets(venue)
        stats = await run_matching(db, cfg, markets, build_verifier(cfg), max_checks)
        print(f"\nКандидатов: {stats.candidates}, уже проверены/помечены: {stats.cached}, "
              f"в очереди на LLM (gross > порога): {stats.queued}")
        print(f"Проверено Claude: {stats.checked}, ошибок: {stats.errors}, "
              f"подтверждено (>= min_confidence): {stats.confirmed}, "
              f"токены in/out: {stats.input_tokens}/{stats.output_tokens}")
        for c, v in sorted(stats.verdicts, key=lambda x: -x[1]["confidence"]):
            mark = "✅" if v["same_event"] else "❌"
            edge = quick_gross_edge(c)
            print(f"\n{mark} conf={v['confidence']:.2f} risk={v['resolution_risk']} "
                  f"yes≡yes={v['yes_equivalent']} gross≈{edge:.3f}")
            print(f"   A [{c.a.venue}] {c.a.question[:100]}")
            print(f"   B [{c.b.venue}] {c.b.question[:100]}")
            for d in v["differences"][:4]:
                print(f"   - {d}")
        print("\nПары в БД:", await db.pair_stats())
    finally:
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument("--llm", type=int, default=None, help="сколько пар проверить через Claude")
    asyncio.run(main(p.parse_args().llm))
