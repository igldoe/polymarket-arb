"""Проверка пары рынков через Claude: одно ли это событие с одинаковыми правилами разрешения."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from ..models import Market

log = logging.getLogger(__name__)

SYSTEM = """You verify whether two prediction-market contracts from different exchanges resolve on exactly the same real-world event, so that buying YES on one and NO on the other is a true hedge. A false "same" loses money on both legs, so be strict: when in doubt, same_event=false or high resolution_risk.

Compare the full resolution rules, not just the titles. Check each of these explicitly:
1. deadline — "by/before" a date vs "on" a date vs "in <year>"; the exact cutoff time and timezone (ET vs UTC can shift the day); market close vs event date.
2. data_source — which source decides (official agency, specific exchange/price feed, specific candle/close vs intraday high, media consensus). Different feeds or price definitions are a difference.
3. cancellation — what happens if the event is cancelled, postponed, delayed, a match is abandoned or goes to overtime/extra time/penalties, a candidate withdraws; does one market resolve NO/50-50/void where the other resolves differently.
4. thresholds — numbers and comparison operators: > vs >=, "above" vs "at least", inclusive/exclusive ranges, units, rounding, which statistic.

Also decide the outcome orientation: yes_equivalent=true if YES on market A wins exactly when YES on market B wins; false if YES on A corresponds to NO on B (e.g. A is "Team X wins", B is "Team Y wins" in a two-way market with no draw). If the outcomes are neither equivalent nor exact complements, same_event=false.

confidence is your probability (0-1) that the hedge is perfect, i.e. both contracts always settle consistently.
resolution_risk: low = identical in substance; medium = minor wording or edge-case differences that are unlikely to matter; high = a plausible scenario settles the two contracts inconsistently.

Write `differences` and the `checks` notes in Russian, concisely; leave `differences` empty if there are none."""


class Checks(BaseModel):
    deadline: str = Field(description="Сравнение сроков, времени и часового пояса")
    data_source: str = Field(description="Сравнение источников разрешения")
    cancellation: str = Field(description="Отмена, перенос, овертайм, снятие кандидата")
    thresholds: str = Field(description="Пороги, операторы сравнения, единицы")


class Verdict(BaseModel):
    same_event: bool
    yes_equivalent: bool
    confidence: float = Field(ge=0, le=1)
    differences: list[str]
    resolution_risk: Literal["low", "medium", "high"]
    checks: Checks


def rules_hash(a: Market, b: Market) -> str:
    """Хэш текстов, на которых основан вердикт: поменялись правила — проверяем заново."""
    blob = json.dumps([a.question, a.rules, a.resolution_source, b.question, b.rules,
                       b.resolution_source], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def describe(label: str, m: Market) -> str:
    close = m.close_time.isoformat() if m.close_time else "unknown"
    ctx = m.extra.get("event_title") or m.extra.get("group_title") or ""
    lines = [
        f"<market_{label}>",
        f"exchange: {m.venue}",
        f"event: {ctx}" if ctx else "",
        f"question: {m.question}",
        f"outcomes: YES = {m.outcomes[0]!r}, NO = {m.outcomes[1]!r}" if len(m.outcomes) == 2 else "",
        f"trading closes: {close}",
        f"expected resolution: {m.extra['expected_expiration']}" if m.extra.get("expected_expiration") else "",
        f"resolution source: {m.resolution_source}" if m.resolution_source else "",
        "rules:",
        m.rules.strip() or "(no rules text provided)",
        f"</market_{label}>",
    ]
    return "\n".join(l for l in lines if l)


@dataclass
class LLMResult:
    verdict: Verdict
    model: str
    input_tokens: int
    output_tokens: int


class LLMVerifier:
    def __init__(self, model: str, effort: str = "medium", max_tokens: int = 8000):
        self.client = anthropic.AsyncAnthropic(max_retries=4)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    async def verify(self, a: Market, b: Market) -> LLMResult:
        prompt = (f"{describe('A', a)}\n\n{describe('B', b)}\n\n"
                  "Are A and B the same event with consistent resolution?")
        response = await self.client.beta.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Verdict,
            output_config={"effort": self.effort},
            # При отказе классификатора запрос автоматически перезапускается на резервной модели.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"модель отказалась: {response.stop_details}")
        if response.parsed_output is None:
            raise RuntimeError(f"нет структурированного ответа (stop_reason={response.stop_reason})")
        return LLMResult(response.parsed_output, response.model,
                         response.usage.input_tokens, response.usage.output_tokens)
