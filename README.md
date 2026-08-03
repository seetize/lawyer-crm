# Beauty Inspector

Telegram-first сервис, который собирает карточку заведения из Яндекс Карт и
2ГИС, проверяет обязательные поля и показывает отчёт по разделам.
Город не зашит в код: по умолчанию используется Астрахань, пользователь может
выбрать любой другой.

## Что работает

- Яндекс Карты — основной источник: название, адрес, описание, категории,
  награды (`Хорошее место`), график, новости, прайс, отзывы и ответы организации;
- пагинация всех доступных текстов отзывов: до 600 последних на Яндексе
  (ограничение самой площадки) и все страницы публичного API 2ГИС;
- объединение отзывов Яндекса и 2ГИС без обрезки до «последних 20»;
- услуги и цены сгруппированы по категориям так, как они опубликованы в
  Яндекс Картах;
- мастера берутся только из явного активного списка карточки Карт; архивные
  сотрудники онлайн-записи в этот отчёт не попадают;
- позиция заведения в органической выдаче Яндекс Карт по заданным рубрикам;
  для небольшого города используется весь город, для крупного — район, для
  Москвы и Санкт-Петербурга — область ближайшего метро;
- коннектор YCLIENTS сохранён в коде, но отключён от отчёта Карт: онлайн-запись,
  её услуги, мастера и слоты будут отдельным контуром;
- бесплатная локальная выжимка по всем собранным отзывам с учётом ответов
  организации;
- Telegram-кнопки `Основное`, `Отзывы`, `Выжимка`, `График`, `Новости`,
  `Услуги и цены`, `Мастера`, `Место в поиске`;
- полные отзывы выдаются страницами, чтобы не упираться в лимит сообщения
  Telegram;
- отсутствующие сведения показываются как
  `данная информация не предоставлена заведением публично`;
- HTTP API `POST /reports` сохранён для будущего фронтенда.

Счётчик площадки может быть больше числа собранных текстов: Яндекс и 2ГИС
учитывают также оценки без текстового отзыва. В отчёте эти значения выводятся
раздельно.

## Агентный конвейер

```text
Telegram / HTTP API
        ↓
Collector — параллельно собирает Яндекс + 2ГИС
        ↓
Reviewer — проверяет рейтинг + отзывы + конкретные цены
        ↓
Review Analyst — строит выжимку по всем текстам и ответам
        ↓
Publisher — отдаёт проверенный профиль Telegram-кнопкам
```

Яндекс всегда идёт первым в `DATA_PROVIDER=multi`, поэтому название, адрес,
описание, основная оценка и ссылка на карту берутся из него. Данные остальных
источников добавляются к профилю, а не заменяют Яндекс.

## Harness разработки

Репозиторий содержит автономный project-scoped harness для следующих задач:

- `AGENTS.md` задаёт обязательный цикл планирования, реализации, независимой
  проверки и фиксации доказательств;
- `.codex/agents/` содержит только независимого read-only reviewer; исследование
  и реализацию ведёт основной агент, а механические проверки выполняют скрипты;
- риск R0-R3 определяет минимальное число агентов и проверок;
- `.harness/memory/` хранит только проверенные архитектурные решения и уроки;
- `scripts/verify.ps1` выполняет единый локальный и CI quality gate;
- `scripts/harness.ps1` запускает задачу автономно через Codex CLI, когда нет
  активного интерактивного чата.

Обычные задачи, отправленные Codex в этом репозитории, автоматически следуют
`AGENTS.md`. Для внешнего unattended-запуска:

```powershell
.\scripts\harness.ps1 -Task "Описание задачи" -Risk auto
```

Harness работает с `approval_policy = never` внутри workspace sandbox: он не
показывает кнопки подтверждения, но и не обходит системные ограничения. Простые
задачи выполняются одним агентом; дополнительные агенты подключаются только
когда независимая проверка оправдывает расход ресурсов.

## Запуск

Требуется Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Минимальная конфигурация рабочего режима:

```env
TELEGRAM_BOT_TOKEN=...
TWOGIS_API_KEY=...
DATA_PROVIDER=multi
DEFAULT_CITY=Астрахань
REVIEW_SUMMARY_PROVIDER=local
YANDEX_RANKING_QUERIES=ногтевая студия,салон красоты
YANDEX_RANKING_MAX_PAGES=20
```

Запуск бота:

```powershell
python -B scripts/run_bot.py
```

Примеры запросов:

```text
gsnv lab
GSNV-LAB Астрахань
gsnv lab в городе Казань
/report Париж | Астрахань | рейтинг, отзывы, цены
```

Регистр, повторяющиеся пробелы и лишние разделители нормализуются. Город можно
выбрать кнопкой или написать прямо в запросе.

HTTP API:

```powershell
uvicorn app.api:app --reload
```

```http
POST /reports
Content-Type: application/json

{
  "query": "Париж",
  "city": "Астрахань",
  "criteria": ["rating", "reviews", "prices"]
}
```

## Источники и ключи

`TWOGIS_API_KEY` нужен для поиска карточки 2ГИС. Ключ публичного API отзывов
берётся из загруженного веб-клиента 2ГИС и не хранится в `.env`.

YCLIENTS сейчас не участвует в сборе отчёта Карт. Переменные оставлены для
будущего отдельного контура онлайн-записи:

```env
YCLIENTS_PARTNER_TOKEN=...
YCLIENTS_USER_TOKEN=...
```

`YANDEX_MAPS_API_KEY` оставлен для последующего перехода на договорной
Organization Search API. В текущем MVP карточка, отзывы, новости, услуги и
поисковая выдача читаются из публичного веб-интерфейса Яндекс Карт, поскольку
официальный Search API не отдаёт весь нужный набор данных. Для промышленной
эксплуатации нужно согласовать такой способ доступа и лимиты с Яндексом.

Локальная выжимка не требует внешнего API:

```env
REVIEW_SUMMARY_PROVIDER=local
REVIEW_SUMMARY_MAX_CHARS=700
```

Для перехода на OpenAI:

```env
OPENAI_API_KEY=...
OPENAI_REVIEWS_ENABLED=true
OPENAI_REVIEW_MODEL=...
REVIEW_SUMMARY_PROVIDER=openai
```

## Проверка

```powershell
python -m pytest -q
```

## SINDY city catalogue (pre-integration QA)

The repository now includes a persistent city catalogue used by Telegram and
the HTTP API before any data is sent to SINDY. Yaroslavl is the pilot city;
categories, bounds and source budgets are configuration rather than hard-coded
workflow branches.

Local QA uses SQLite by default. Production-compatible PostgreSQL/PostGIS is
defined in `docker-compose.catalog.yml`; set `CATALOG_DATABASE_URL` to its
SQLAlchemy URL before running migrations.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -B scripts\catalog_refresh.py
powershell -ExecutionPolicy RemoteSigned -File scripts\install_catalog_refresh.ps1
```

The scheduled refresh runs every six hours, but weekly discovery jobs are
idempotent. Detail enrichment is bounded, unchanged passport snapshots are not
written again, and deterministic competitor calculation does not call an LLM.
Telegram QA commands are `/city_status`, `/catalog`, `/passport` and
`/competitors`. Mutating commands `/crawl_city` and `/refresh_city` fail closed
unless the sender is listed in `TELEGRAM_ADMIN_IDS`.

The Telegram catalogue is DB-first: category selection is followed by city,
district or available metro scope, then deduplicated location buttons and a
stored passport. Districts and metro stations are durable catalogue entities;
typed zone names are normalized before lookup. Browsing never calls map
providers. The `Сравнить` button calculates a grounded, token-free comparison
from saved categories, coordinates, services, ratings and review counts.

HTTP readiness and catalogue endpoints:

- `GET /health/live` and `GET /health/ready`;
- `GET /v1/catalog/status`;
- `GET /v1/locations` and `GET /v1/locations/{id}`;
- `GET /v1/locations/{id}/competitors`.

No SINDY core writes are enabled yet. That boundary is intentionally gated by
the acceptance plan in `docs/SINDY_ACCEPTANCE.md`.

Тесты покрывают нормализацию Яндекса, подпись и ответы организаций, объединение
источников, 2ГИС, публичный AES-GCM контекст YCLIENTS, Telegram-пагинацию и
владение кэшем отчёта.

## Следующие производственные шаги

1. PostgreSQL/Redis для истории профилей и общего кэша нескольких процессов.
2. Очередь фоновых задач, rate limiting и повторные попытки с backoff.
3. Webhook вместо polling и наблюдаемость по каждому источнику.
4. Договорные API-доступы Яндекса и YCLIENTS для гарантированного SLA.
