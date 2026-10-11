"""Дешёвый предфильтр пар рынков между двумя площадками.

1. Блокировка через инвертированный индекс по редким токенам (имена, числа, слова),
   чтобы не сравнивать 100k x 80k рынков попарно.
2. Близкая дата закрытия.
3. Жёсткие проверки сущностей: числа совпадают, даты/годы не противоречат, имена пересекаются.
4. Fuzzy-сходство текста (rapidfuzz).
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import Market
from .entities import Entities, extract, match_text, normalize, subject_text

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    a: Market
    b: Market
    score: float          # fuzzy-сходство 0..100
    reasons: list[str]    # что совпало (для отладки и сообщений)

    @property
    def key(self) -> tuple[str, str]:
        return pair_key(self.a, self.b)


def pair_key(a: Market, b: Market) -> tuple[str, str]:
    return tuple(sorted((a.key, b.key)))  # type: ignore[return-value]


@dataclass
class Prepared:
    market: Market
    text: str
    ent: Entities
    subject: frozenset[str]   # имена из конкретной части рынка

    def tokens(self) -> set[str]:
        return ({f"n:{n}" for n in self.ent.names} | {f"#:{x:g}" for x in self.ent.numbers}
                | {f"w:{w}" for w in self.ent.words})


def prepare(markets: list[Market]) -> list[Prepared]:
    out = []
    for m in markets:
        text = match_text(m)
        out.append(Prepared(m, normalize(text), extract(text), extract(subject_text(m)).names))
    return out


def entities_compatible(a: Entities, b: Entities) -> tuple[bool, list[str]]:
    reasons = []
    if a.kinds != b.kinds:
        return False, ["разный тип исхода"]
    if a.numbers or b.numbers:
        if a.numbers != b.numbers:
            return False, ["числа различаются"]
        reasons.append("числа: " + ", ".join(f"{x:g}" for x in sorted(a.numbers)))
    if a.dates and b.dates:
        if a.dates != b.dates:
            return False, ["даты различаются"]
        reasons.append("даты совпадают")
    if a.years and b.years and not (a.years & b.years):
        return False, ["годы различаются"]
    shared = a.names & b.names
    if a.names and b.names:
        if not shared:
            return False, ["нет общих имён"]
        if len(shared) / len(a.names | b.names) < 0.4:
            return False, ["мало общих имён"]
        reasons.append("имена: " + ", ".join(sorted(shared)))
    return True, reasons


class Prefilter:
    def __init__(self, close_tolerance_days: float = 7, min_score: float = 70,
                 max_token_df: int = 1500, top_k: int = 3, max_shortlist: int = 60):
        self.tol = close_tolerance_days * 86400
        self.min_score = min_score
        self.max_token_df = max_token_df
        self.top_k = top_k
        self.max_shortlist = max_shortlist

    def find(self, side_a: list[Market], side_b: list[Market]) -> list[Candidate]:
        """Кандидаты в пары между рынками двух разных площадок."""
        if len(side_a) > len(side_b):  # индексируем бо́льшую сторону, итерируем меньшую
            side_a, side_b = side_b, side_a
        prep_b = prepare(side_b)
        index: dict[str, list[int]] = defaultdict(list)
        for i, p in enumerate(prep_b):
            for t in p.tokens():
                index[t].append(i)
        index = {t: ids for t, ids in index.items() if len(ids) <= self.max_token_df}

        out: list[Candidate] = []
        for pa in prepare(side_a):
            if not pa.market.close_time:
                continue
            hits: Counter[int] = Counter()
            for t in pa.tokens():
                for i in index.get(t, ()):
                    hits[i] += 1
            found = []
            for i, _ in hits.most_common(self.max_shortlist):
                pb = prep_b[i]
                if not pb.market.close_time:
                    continue
                if abs((pa.market.close_time - pb.market.close_time).total_seconds()) > self.tol:
                    continue
                ok, reasons = entities_compatible(pa.ent, pb.ent)
                if not ok:
                    continue
                if pa.subject and pb.subject and not (pa.subject & pb.subject):
                    continue  # один матч, но разные команды/кандидаты
                if (pa.subject - pb.ent.names) and (pb.subject - pa.ent.names):
                    continue  # у каждого свой субъект: "Trump visit Utah" vs "... New Hampshire"
                score = fuzz.token_set_ratio(pa.text, pb.text)
                if score < self.min_score:
                    continue
                found.append(Candidate(pa.market, pb.market, score, reasons))
            found.sort(key=lambda c: c.score, reverse=True)
            out.extend(found[: self.top_k])
        log.info("предфильтр %s×%s: %d кандидатов",
                 side_a[0].venue if side_a else "?", side_b[0].venue if side_b else "?", len(out))
        return out


def quick_gross_edge(c: Candidate) -> float | None:
    """Ориентировочный спред по ценам из листингов (без стакана и комиссий):
    1 - min(YES_a + NO_b, NO_a + YES_b). None, если цен нет."""
    ea, eb = c.a.extra, c.b.extra
    sums = []
    if ea.get("yes_ask") and eb.get("no_ask"):
        sums.append(ea["yes_ask"] + eb["no_ask"])
    if ea.get("no_ask") and eb.get("yes_ask"):
        sums.append(ea["no_ask"] + eb["yes_ask"])
    return 1 - min(sums) if sums else None
