# Каталог для проверки парсинга

В репозиторий включён снимок реальных собранных данных [catalog.sqlite3.gz](catalog.sqlite3.gz). Он подготовлен из серверной резервной копии от 14 ноября 2026 года UTC. Это исторический снимок для проверки происхождения и структуры данных. Актуальное расписание и наличие билетов проверяются через [сайт](https://kiber102.vkkasher.ru/) и ссылки организаторов.

## Что находится в базе

| Таблица | Записей | Назначение |
|---|---:|---|
| `events` | 78 | Мероприятия, даты, площадки, цены, возраст, изображения и статусы |
| `sources` | 3 | Подключённые источники и результаты последней проверки снимка |
| `event_sources` | 148 | Ссылки, связывающие мероприятия с источниками |
| `provider_identities` | 78 | Идентификаторы событий у поставщиков для повторной загрузки без дублей |
| `event_ticket_evidence` | 52 | Сверка конкретных сеансов Кассы.ру, изображения и наличие билетов |
| `raw_source_records` | 3116 | Исходные записи, URL, хеши, результат обработки и причины отклонений |
| `alembic_version` | 1 | Версия схемы снимка |

Среди 52 проверенных сеансов 50 отмечены как доступные и 2 как распроданные. Распроданные записи сохранены для проверки, приложение исключает их из подборок. Статус `published` сам по себе не означает наличие билетов. Неизвестные цены, координаты и время окончания сохранены без заполнения вымышленными значениями.

База создана заново с переносом только перечисленных таблиц. Переписки, трассы с пользовательскими запросами, контакты, подписки, транспортные адреса, квоты, расходные записи и административный журнал не переносились. Соответствующие таблицы существуют для совместимости приложения, но пусты. Конфигураций, `.env` и состояния Telegram-бота в архиве нет.

## Распаковка и проверка

Достаточно Python 3, зависимости проекта для распаковки не нужны. Выполните из корня клона. Каталог назначения должен быть новым, чтобы не перезаписать рабочие данные.

```python
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

metadata = json.loads(Path("data/manifest.json").read_text(encoding="utf-8"))
archive = Path("data/catalog.sqlite3.gz")
assert hashlib.sha256(archive.read_bytes()).hexdigest() == metadata["archive"]["sha256"]
review = Path("../kiber102-catalog-review")
review.mkdir()
database = review / "catalog.sqlite3"
with gzip.open(archive, "rb") as source, database.open("xb") as target:
    shutil.copyfileobj(source, target)
assert hashlib.sha256(database.read_bytes()).hexdigest() == metadata["database"]["sha256"]
with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    print(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
```

Ожидаемый результат: `78`. Контрольные суммы, размеры, дата подготовки и количества по всем таблицам указаны в [manifest.json](manifest.json). Проверены целостность SQLite, внешние ключи и отсутствие строк в пользовательских таблицах.

## Посмотреть результат сбора

Откройте распакованную базу любым SQLite-клиентом в режиме чтения. Примеры запросов:

```sql
-- Реальные мероприятия и ссылки на первоисточники.
SELECT title, city, venue, date_start, price_min, price_max, status, source_url
FROM events
ORDER BY date_start, title;

-- Сколько данных сохранилось от каждого источника.
SELECT source_url, outcome, COUNT(*) AS records
FROM raw_source_records
GROUP BY source_url, outcome
ORDER BY records DESC;

-- Наличие билетов и изображения конкретных сеансов.
SELECT e.title, e.date_start, t.availability, t.image_url, t.source_url, t.checked_at
FROM event_ticket_evidence AS t
JOIN events AS e ON e.id = t.event_id
ORDER BY e.date_start;

-- Связь карточек с первоисточниками.
SELECT e.title, s.name, s.url, s.verified_at
FROM event_sources AS s
JOIN events AS e ON e.id = s.event_id
ORDER BY e.title;
```

Для отдельного SQLite-экземпляра укажите в собственном `.env` абсолютный путь к копии базы через `DATABASE_URL=sqlite:////absolute/path/catalog.sqlite3`, затем примените миграции по [инструкции](../docs/deployment.md). На Windows используйте SQLAlchemy URL вида `sqlite:///C:/path/catalog.sqlite3`. Не меняйте базу действующего сервера и не подключайте к проверочной копии токен работающего бота. Compose использует PostgreSQL, для него нужен отдельный проверенный перенос данных.
