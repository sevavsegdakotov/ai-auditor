# ai-аналитик

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
# заполните OPENAI_API_KEY и (при необходимости) KEYSO_API_TOKEN / GOOGLE_SHEETS_WEBHOOK_URL в .env
docker compose up --build
```

Откройте `http://localhost:8000`.

Для запуска в фоне:

```bash
docker compose up -d --build
```

Остановить:

```bash
docker compose down
```

## Автодеплой (GitHub Actions)

Workflow: `.github/workflows/deploy.yml`  
Триггеры: push в `main` и ручной запуск (`workflow_dispatch`).

Добавьте в GitHub репозитория Secrets:

- `VPS_HOST` — `193.176.190.9`
- `VPS_PORT` — `22` (опционально)
- `VPS_USER` — `dev`
- `VPS_DEPLOY_PATH` — `/home/dev/apps/ainalytic`
- `VPS_SSH_KEY` — приватный ключ для доступа к серверу (содержимое файла, например `~/.ssh/id_ed25519_vps`)

Важно:

- На сервере должен существовать `.env` в папке деплоя (`/home/dev/apps/ainalytic/.env`).
- Workflow не перетирает `.env` и `data/`.

## Важные детали

- Приложение ожидает, что в Excel есть колонки URL и посещаемости (распознаются эвристически по названиям).
- Отчеты сохраняются в `data/report_<run_id>.json`.
- Скриншоты сохраняются в `data/screenshots/<run_id>/`.

## Ограничения текущего MVP

- Нет очереди задач/фонового воркера: анализ идет в рамках одного HTTP-запроса.
- Без авторизации и без хранения истории в БД.
- Ошибки конкретных страниц не останавливают весь запуск, но фиксируются в отчете.
