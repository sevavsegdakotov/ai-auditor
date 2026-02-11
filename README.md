# Metrika Auditor

Веб-приложение для автоматизированного аудита:
1. Принимает несколько Excel-выгрузок из Яндекс.Метрики.
2. Делает первичный AI-анализ данных через OpenAI API.
3. Выбирает топ-страницы по посещаемости.
4. Открывает эти страницы, делает full-page desktop/mobile скриншоты и вытягивает текст.
5. Делает финальный AI-анализ всех артефактов.

## Запуск локально

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# заполните OPENAI_API_KEY в .env
uvicorn app.main:app --reload
```

Откройте `http://localhost:8000`.

## Запуск в Docker

```bash
cp .env.example .env
# заполните OPENAI_API_KEY в .env
docker compose up --build
```

## Важные детали

- Приложение ожидает, что в Excel есть колонки URL и посещаемости (распознаются эвристически по названиям).
- Отчеты сохраняются в `data/report_<run_id>.json`.
- Скриншоты сохраняются в `data/screenshots/<run_id>/`.

## Ограничения текущего MVP

- Нет очереди задач/фонового воркера: анализ идет в рамках одного HTTP-запроса.
- Без авторизации и без хранения истории в БД.
- Ошибки конкретных страниц не останавливают весь запуск, но фиксируются в отчете.
