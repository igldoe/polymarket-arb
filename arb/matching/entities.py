"""Нормализация текста и извлечение ключевых сущностей: числа, даты, годы, имена."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..models import Market

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
STOPWORDS = {
    "will", "the", "a", "an", "of", "in", "on", "at", "by", "to", "for", "be", "is", "are", "and", "or",
    "before", "after", "than", "this", "that", "with", "from", "as", "yes", "no", "who", "what", "which",
    "how", "win", "wins", "won", "vs", "versus", "more", "less", "above", "below", "over", "under",
    "between", "price", "end", "close", "day", "week", "month", "year", "any", "it", "its", "his", "her",
    "their", "do", "does", "did", "have", "has", "not", "game", "match", "market", "next", "first",
    "least", "most", "exactly", "or", "range", "other", "new", "out", "up", "down", "happen",
    "where", "when", "why", "whom", "whose", "decision", "spread", "map", "winner", "total",
    "team", "teams", "both", "neither", "draw", "score", "goal", "half", "points", "fc",
}

# Тип исхода внутри одного события: у двух рынков он должен совпадать.
KINDS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("draw", r"\b(draw|tie|tied)\b"),
    ("btts", r"\bboth (teams )?(to )?score\b"),
    ("no_goal", r"\b(no goal|neither team)\b"),
    ("first_score", r"\b(score first|scores first|first goal|first to score|to score first)\b"),
    ("spread", r"\b(spread|wins? by (over|more than)|handicap)\b|\([+-]\d+(\.\d+)?\)"),
    ("total", r"\b(total|o/u|over/under|combined)\b"),
    ("half", r"\b(first|second|1st|2nd) half\b|\b[12]h\b|\bhalf-?time\b"),
    ("exact_score", r"\b(exact|final|correct) score\b|\b\d+\s?-\s?\d+\?"),
    ("leader", r"\b(top (batter|scorer|goalscorer|bowler)|leader|most \w+ yards|mvp)\b"),
    ("map", r"\bmap \d\b"),
    ("runner_up", r"\brunner[- ]up\b"),
    ("relegated", r"\brelegat"),
    ("top_n", r"\btop \d+\b|\bfinish \d"),
    ("player_prop", r"\b(yards|rebounds|assists|strikeouts|touchdowns?|receptions)\b"),
]]
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
# "October 15", "Oct 8, 2026", "15 October"
_DATE_MD = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(\d{{4}}))?", re.I)
_DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\b(?:,?\s*(\d{{4}}))?", re.I)
_DATE_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DATE_US = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")
_NUMBER = re.compile(
    r"(?<![\w.])([$€£])?\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"\s?(k|m|b|bn|million|billion|thousand|trillion|%|bps|st|nd|rd|th)?(?![\w])",
    re.I,
)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
         "billion": 1e9, "trillion": 1e12}
_WORD = re.compile(r"[a-z0-9]+")
# Имена: слова с заглавной буквы и названия вида "49ers", "76ers".
_NAME = re.compile(r"\b(?:[A-Z][\w'’.-]*\w|\d+[a-z]{2,}\w*)")
_ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)")


def fold(text: str) -> str:
    """Нижний регистр без диакритики: "Inácio" → "inacio"."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def normalize(text: str) -> str:
    return " ".join(_WORD.findall(fold(text)))


def kinds(text: str) -> frozenset[str]:
    return frozenset(k for k, rx in KINDS if rx.search(text))


@dataclass
class Entities:
    numbers: frozenset[float] = field(default_factory=frozenset)
    dates: frozenset[tuple[int, int]] = field(default_factory=frozenset)   # (месяц, день)
    years: frozenset[int] = field(default_factory=frozenset)
    names: frozenset[str] = field(default_factory=frozenset)
    words: frozenset[str] = field(default_factory=frozenset)               # значимые слова
    kinds: frozenset[str] = field(default_factory=frozenset)               # тип исхода


def extract(text: str) -> Entities:
    dates: set[tuple[int, int]] = set()
    years: set[int] = set()
    rest = text

    def take_date(m: re.Match, month: int, day: int, year: str | None) -> str:
        if 1 <= month <= 12 and 1 <= day <= 31:
            dates.add((month, day))
        if year:
            years.add(int(year) if len(year) == 4 else 2000 + int(year))
        return " "

    rest = _DATE_ISO.sub(lambda m: take_date(m, int(m[2]), int(m[3]), m[1]), rest)
    rest = _DATE_MD.sub(lambda m: take_date(m, MONTHS[m[1].lower()], int(m[2]), m[3]), rest)
    rest = _DATE_DM.sub(lambda m: take_date(m, MONTHS[m[2].lower()], int(m[1]), m[3]), rest)
    rest = _DATE_US.sub(lambda m: take_date(m, int(m[1]), int(m[2]), m[3]), rest)
    for y in _YEAR.findall(rest):
        years.add(int(y))
    rest = _YEAR.sub(" ", rest)

    numbers: set[float] = set()
    for cur, num, suffix in _NUMBER.findall(rest):
        value = float(num.replace(",", ""))
        value *= _MULT.get((suffix or "").lower(), 1)
        numbers.add(round(value, 4))

    names = set()
    for tok in _NAME.findall(text):
        low = re.sub(r"['’]s$", "", fold(tok).strip("."))
        if _ORDINAL.fullmatch(low):
            continue
        if low not in STOPWORDS and low not in MONTHS and low not in WEEKDAYS and len(low) > 1:
            names.add(low)
    words = {w for w in normalize(rest).split()
             if len(w) >= 3 and w not in STOPWORDS and w not in MONTHS and not w.isdigit()}
    return Entities(frozenset(numbers), frozenset(dates), frozenset(years),
                    frozenset(names), frozenset(words), kinds(text))


def match_text(m: Market) -> str:
    """Текст для сравнения: вопрос плюс заголовок события/группы, если он добавляет контекст."""
    ctx = m.extra.get("event_title") or m.extra.get("group_title") or ""
    if ctx and normalize(ctx) not in normalize(m.question):
        return f"{ctx} — {m.question}"
    return m.question


def subject_text(m: Market) -> str:
    """Конкретная часть рынка без заголовка события: команда, кандидат, порог."""
    if m.venue == "polymarket":
        if m.outcomes and m.outcomes[0].lower() != "yes":
            return m.outcomes[0]
        return m.extra.get("group_item") or m.question
    if m.venue == "kalshi":
        return m.extra.get("yes_sub_title") or m.question
    if m.venue == "limitless" and m.extra.get("group_title"):
        return m.question.split(" — ", 1)[-1]
    return m.question
