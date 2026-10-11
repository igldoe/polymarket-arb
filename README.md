# 🎯 polymarket-arb

**🇬🇧 English** · [🇷🇺 Русский](#-polymarket-arb-ru)

> Hunts prediction markets for moments when you can buy **YES** on one exchange and **NO** on another for less than **$1** total. Whatever happens, the payout is $1.

An arbitrage scanner across **Polymarket**, **Kalshi** and **Limitless**. It only reads public data: no trading-account keys, no auto-trading, it never buys anything. It just watches and points.

```
Polymarket:  "Will Celtic win?"  YES = $0.41
Limitless:   "Will Celtic win?"  NO  = $0.55
                                 ─────────────
                          total  $0.96  →  guaranteed $1.00 back
                                           (minus fees, more on that below)
```

---

## 🤔 What's the catch

The catch is that **"similar" markets aren't always the same market**.

| Exchange A | Exchange B | Same thing? |
|---|---|---|
| Will Bitcoin be above $90,000 on Oct 15? | BTC ≥ $90,000 on Oct 15? | 🤨 `>` vs `>=`, and the snapshot time may differ |
| Trump visits Utah in 2026? | Trump visits New Hampshire before 2027? | ❌ different states |
| Galaxy wins 3-1? | St. Louis 3 - 1 Galaxy? | ❌ same score, different winner |
| Celtic wins | UEL, Benfica vs Celtic — Celtic | ✅ yes |

Buying two legs on two different events is the fastest way to lose both. That's why most of this project is about matching events, not about finding cheap prices.

---

## ⚙️ How it works

```
 ┌────────────┐  ┌────────────┐  ┌────────────┐
 │ Polymarket │  │   Kalshi   │  │ Limitless  │     ~255,000 open markets
 └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
       └───────────────┼───────────────┘
                       ▼
              📦 SQLite: one unified Market format
                       ▼
     🔍 Prefilter: dates, numbers, names, outcome type, rapidfuzz
                       ▼                              ~7,000 lookalike pairs
     💸 Price gate: YES + NO < $1 at listing prices?
                       ▼                              ~1,000 promising ones
     🧠 Claude reads the FULL rules on both sides
        and compares deadlines, sources, postponements, thresholds
                       ▼
     📒 Verdict cache (never pay twice for the same pair)
                       ▼
     📈 Order-book math with fees                 (in progress)
                       ▼
     📲 Telegram: only actually executable arbs    (in progress)
```

### 1. Exchange adapters
Every exchange has its own API and its own quirks. Kalshi only returns bids, Limitless order-book sizes are scaled by a million, and Polymarket sometimes lists games with team-name outcomes instead of Yes/No. Adapters flatten all of that into one shape:

```python
Market(venue, id, question, rules, close_time, resolution_source, outcomes, url, extra)
OrderBook(yes_asks=[Level(price, size), ...], no_asks=[...])
```

A new exchange is one new file in `arb/adapters/` with two methods: `fetch_markets()` and `fetch_book()`.

### 2. Prefilter
Comparing 190,000 × 65,000 markets pairwise is about 12 billion comparisons. So it first builds an inverted index on rare tokens, and a pair only moves on if:

- 📅 close dates are at most N days apart;
- 🔢 **numbers match exactly** (`$90k` == `$90,000`, but ≠ `$91,000`);
- 🏷️ the outcome type matches: win ≠ draw ≠ both teams to score ≠ spread ≠ total;
- 👤 the subject is shared: same team, same candidate, same city;
- 🔤 rapidfuzz text similarity is above the threshold.

### 3. Verification with Claude
For every suspicious pair the model gets both questions and **the full resolution rules** and returns strict JSON:

```json
{
  "same_event": true,
  "yes_equivalent": true,
  "confidence": 0.93,
  "resolution_risk": "low",
  "differences": ["Kalshi snapshots the price at 17:00 ET, Polymarket at 12:00 ET"],
  "checks": { "deadline": "…", "data_source": "…", "cancellation": "…", "thresholds": "…" }
}
```

`yes_equivalent: false` means YES on one exchange corresponds to NO on the other, e.g. "Team X wins" vs "Team Y wins".

---

## 🚀 Getting started

```bash
git clone https://github.com/igldoe/polymarket-arb.git
cd polymarket-arb
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
```

```bash
# 1. Download all markets into SQLite (~2 minutes)
.venv/bin/python -m arb.fetch

# 2. Find pairs and verify the 20 most promising ones with Claude
.venv/bin/python -m arb.match --llm 20
```

Without `ANTHROPIC_API_KEY` step 2 still runs, but only the prefilter, without model verification.

### Configuration

**`.env`** — secrets, never committed:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | key for pair verification |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` (most accurate) / `claude-sonnet-5-5` / `claude-haiku-5-5` (cheapest) |
| `ANTHROPIC_EFFORT` | `low` / `medium` / `high` — how hard the model thinks about each pair |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for notifications (coming soon) |

**`config.yaml`** — thresholds and limits:

| Setting | Default | What it does |
|---|---|---|
| `scan.max_days_to_close` | 120 | ignore markets that close later than this |
| `matching.close_tolerance_days` | 7 | allowed close-date gap within a pair |
| `matching.min_fuzzy_score` | 70 | text similarity threshold |
| `matching.max_llm_checks_per_cycle` | 100 | 💰 cap on model spend |
| `matching.min_confidence` | 0.9 | below this confidence an arb is ignored |
| `venues.*.rate_per_sec` | 25 / 8 / 1 | request rate limit per exchange |

---

## 🗺️ Status

- [x] Polymarket, Kalshi, Limitless adapters + SQLite
- [x] Event matching: prefilter + Claude + cache
- [ ] Order-book arb math: max profitable size, fees, APR
- [ ] Telegram bot: alerts, `/top`, `/status`, `/settings`, `/confirm`, `/reject`
- [ ] Tests on fixtures from real API responses
- [ ] Docker + docker-compose for a VPS

---

## 📁 Layout

```
arb/
├── adapters/          # one file per exchange + shared interface
├── matching/
│   ├── entities.py    # numbers, dates, names, outcome type
│   ├── prefilter.py   # cheap candidate selection
│   ├── llm.py         # pair verification with Claude
│   └── pipeline.py    # glue + cache
├── http.py            # rate limiting + retries with backoff
├── db.py              # SQLite: markets and pairs
├── fetch.py           # python -m arb.fetch
└── match.py           # python -m arb.match
```

---

## ⚠️ Honest limitations

- **Not financial advice.** The scanner finds candidates; you make the call.
- **Prices move.** By the time you open the second exchange the arb may be gone. Executing two legs is not atomic.
- **The model can be wrong.** That's why `resolution_risk` and the list of differences are shown, and a bad pair can be flagged manually.
- **Your money is locked until resolution.** 2% in a week and 2% in six months are very different trades, hence APR on the roadmap.
- **Access depends on your country.** Kalshi serves the US, Polymarket is restricted in several jurisdictions. Check where you're allowed to trade.
- **Fees and withdrawals** eat thin arbs. Fee-aware math is the next stage.

---

<p align="center"><i>Buy low here, sell high there, or better yet, do both at once.</i> 🎲</p>

<br>

---
---

<a id="-polymarket-arb-ru"></a>
# 🎯 polymarket-arb

[🇬🇧 English](#-polymarket-arb) · **🇷🇺 Русский**

> Ищет на рынках прогнозов места, где можно купить **YES** на одной площадке и **NO** на другой дешевле чем за **$1**. Какое бы событие ни наступило, выплата будет $1.

Сканер арбитражных вилок между **Polymarket**, **Kalshi** и **Limitless**. Читает только публичные данные: ключей от торговых аккаунтов нет, автоторговли нет, ничего не покупает. Только смотрит и подсказывает.

```
Polymarket:  «Celtic победит?»  YES = $0.41
Limitless:   «Celtic победит?»  NO  = $0.55
                                ─────────────
                         итого  $0.96  →  гарантированно получаешь $1.00
                                          (минус комиссии, но об этом ниже)
```

---

## 🤔 В чём подвох

Подвох в том, что **«похожие» рынки не всегда одинаковые**.

| Площадка A | Площадка B | Одно и то же? |
|---|---|---|
| Will Bitcoin be above $90,000 on Oct 15? | BTC ≥ $90,000 on Oct 15? | 🤨 `>` против `>=`, и время фиксации может различаться |
| Trump visits Utah in 2026? | Trump visits New Hampshire before 2027? | ❌ разные штаты |
| Galaxy wins 3-1? | St. Louis 3 - 1 Galaxy? | ❌ счёт тот же, победитель другой |
| Celtic wins | UEL, Benfica vs Celtic — Celtic | ✅ да |

Купить две ноги на разные события — самый быстрый способ проиграть обе. Поэтому большая часть проекта занимается сопоставлением событий, а не поиском дешёвых цен.

---

## ⚙️ Как это работает

```
 ┌────────────┐  ┌────────────┐  ┌────────────┐
 │ Polymarket │  │   Kalshi   │  │ Limitless  │     ~255 000 открытых рынков
 └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
       └───────────────┼───────────────┘
                       ▼
              📦 SQLite: единый формат Market
                       ▼
     🔍 Предфильтр: даты, числа, имена, тип исхода, rapidfuzz
                       ▼                              ~7 000 похожих пар
     💸 Ценовой фильтр: YES + NO < $1 по листингу?
                       ▼                              ~1 000 перспективных
     🧠 Claude читает ПОЛНЫЕ правила обеих сторон
        и сравнивает дедлайны, источники, переносы, пороги
                       ▼
     📒 Кэш вердиктов (повторно за ту же пару не платим)
                       ▼
     📈 Расчёт по стаканам с комиссиями          (в работе)
                       ▼
     📲 Telegram: только реально исполнимые вилки (в работе)
```

### 1. Адаптеры площадок
У каждой площадки свой API и свои странности. Например, Kalshi отдаёт только биды, у Limitless объёмы в стакане умножены на миллион, у Polymarket матчи бывают с исходами-командами вместо Yes/No. Адаптеры приводят всё это к одному виду:

```python
Market(venue, id, question, rules, close_time, resolution_source, outcomes, url, extra)
OrderBook(yes_asks=[Level(price, size), ...], no_asks=[...])
```

Новая площадка — это один новый файл в `arb/adapters/` с двумя методами: `fetch_markets()` и `fetch_book()`.

### 2. Предфильтр
Сравнить 190 000 × 65 000 рынков попарно — около 12 миллиардов сравнений. Поэтому сначала строится инвертированный индекс по редким токенам, и пара проходит дальше, только если:

- 📅 даты закрытия отличаются не больше чем на N дней;
- 🔢 **числа совпадают в точности** (`$90k` == `$90,000`, но ≠ `$91,000`);
- 🏷️ тип исхода одинаковый: победа ≠ ничья ≠ «обе забьют» ≠ фора ≠ тотал;
- 👤 субъект общий: та же команда, тот же кандидат, тот же город;
- 🔤 сходство текста по rapidfuzz выше порога.

### 3. Проверка через Claude
Для каждой подозрительной пары модель получает оба вопроса и **полные тексты правил** и возвращает строгий JSON:

```json
{
  "same_event": true,
  "yes_equivalent": true,
  "confidence": 0.93,
  "resolution_risk": "low",
  "differences": ["Kalshi фиксирует цену в 17:00 ET, Polymarket в 12:00 ET"],
  "checks": { "deadline": "…", "data_source": "…", "cancellation": "…", "thresholds": "…" }
}
```

`yes_equivalent: false` означает, что YES на одной площадке соответствует NO на другой, например «Team X wins» и «Team Y wins».

---

## 🚀 Запуск

```bash
git clone https://github.com/igldoe/polymarket-arb.git
cd polymarket-arb
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # и вписать ключи
```

```bash
# 1. Скачать все рынки в SQLite (~2 минуты)
.venv/bin/python -m arb.fetch

# 2. Найти пары и проверить 20 самых перспективных через Claude
.venv/bin/python -m arb.match --llm 20
```

Без `ANTHROPIC_API_KEY` второй шаг тоже работает, но только предфильтр, без проверки моделью.

### Настройки

**`.env`** — секреты, в git не попадает:

| Переменная | Зачем |
|---|---|
| `ANTHROPIC_API_KEY` | ключ для проверки пар |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` (точнее) / `claude-sonnet-5-5` / `claude-haiku-5-5` (дешевле) |
| `ANTHROPIC_EFFORT` | `low` / `medium` / `high` — сколько модель думает над парой |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | для уведомлений (скоро) |

**`config.yaml`** — пороги и лимиты:

| Параметр | По умолчанию | Что делает |
|---|---|---|
| `scan.max_days_to_close` | 120 | не смотреть рынки, которые закрываются дальше этого срока |
| `matching.close_tolerance_days` | 7 | допустимая разница дат закрытия в паре |
| `matching.min_fuzzy_score` | 70 | порог похожести текста |
| `matching.max_llm_checks_per_cycle` | 100 | 💰 ограничение расходов на модель |
| `matching.min_confidence` | 0.9 | ниже этой уверенности вилка не считается |
| `venues.*.rate_per_sec` | 25 / 8 / 1 | лимит запросов к каждой площадке |

---

## 🗺️ Статус

- [x] Адаптеры Polymarket, Kalshi, Limitless + SQLite
- [x] Сопоставление событий: предфильтр + Claude + кэш
- [ ] Расчёт вилки по стаканам: максимальный прибыльный объём, комиссии, APR
- [ ] Telegram-бот: уведомления, `/top`, `/status`, `/settings`, `/confirm`, `/reject`
- [ ] Тесты на фикстурах реальных ответов API
- [ ] Docker + docker-compose для VPS

---

## 📁 Структура

```
arb/
├── adapters/          # по файлу на площадку + общий интерфейс
├── matching/
│   ├── entities.py    # числа, даты, имена, тип исхода
│   ├── prefilter.py   # дешёвый отбор кандидатов
│   ├── llm.py         # проверка пары через Claude
│   └── pipeline.py    # всё вместе + кэш
├── http.py            # rate limit + ретраи с backoff
├── db.py              # SQLite: рынки и пары
├── fetch.py           # python -m arb.fetch
└── match.py           # python -m arb.match
```

---

## ⚠️ Честно об ограничениях

- **Это не финансовый совет.** Сканер ищет кандидатов, решение принимаете вы.
- **Цены живые.** Пока вы открываете вторую площадку, вилка может исчезнуть. Исполнение двух ног не атомарно.
- **Модель может ошибиться.** Поэтому показываются `resolution_risk` и список отличий, а неверную пару можно пометить вручную.
- **Деньги заморожены до разрешения.** 2% за неделю и 2% за полгода — очень разные сделки, поэтому в планах APR.
- **Доступ к площадкам зависит от страны.** Kalshi работает для США, Polymarket ограничен в ряде юрисдикций. Проверьте, где вам можно торговать.
- **Комиссии и вывод средств** съедают тонкие вилки. Расчёт с комиссиями будет на следующем этапе.

---

<p align="center"><i>Купи дёшево там, продай дорого тут, а лучше и то и другое сразу.</i> 🎲</p>
