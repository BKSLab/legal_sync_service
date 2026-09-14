# Legal Sync Service

Сервис мониторинга изменений нормативных актов и подготовки обновлений для RAG Service.

## Быстрый старт

1. Создать PostgreSQL-базу, например `legal_sync_service_local`.
2. Заполнить `.env` по образцу `.env.example`.
3. Установить зависимости:

```bash
pip install -r requirements-dev.txt
```

4. Применить миграции:

```bash
alembic upgrade head
```

5. Запустить приложение:

```bash
hypercorn app.main:app --reload
```

API будет доступен на `http://localhost:8000`, Swagger UI — на `/docs`, админка — на `/admin`.

При запуске Python вне Docker укажите `RAG_SERVICE_BASE_URL=http://localhost:8002`,
если RAG опубликован на порту `8002` его стандартным Compose.

## Развёртывание через Docker Compose

Нужны Docker Engine и Docker Compose v2 или новее. Создайте `.env` из
`.env.example` и задайте пароль PostgreSQL, ключ сессий `SECRET_KEY`, логин и
пароль администратора, `API_KEY` и `RAG_SERVICE_API_KEY`.

Compose создаёт базу с именем `POSTGRES_NAME`, подключает приложение к `db:5432`
и ждёт готовности PostgreSQL перед запуском миграций. Порт БД на хост не
публикуется. Данные сохраняются в томе `pg_legal_sync_data`.

Для Legal Sync и RAG на одном Docker-сервере используются опубликованные порты:

| Где задать | Переменная | Значение |
| --- | --- | --- |
| Legal Sync `.env` | `LEGAL_SYNC_PORT` | `8000` |
| Legal Sync `.env` | `RAG_SERVICE_BASE_URL` | `http://host.docker.internal:8002` |
| Legal Sync `.env` | `RAG_SERVICE_API_KEY` | Значение `API_KEY` из RAG |
| RAG `.env` | `LEGAL_SYNC_ENABLED` | `true` |
| RAG `.env` | `LEGAL_SYNC_BASE_URL` | `http://host.docker.internal:8000` |
| RAG `.env` | `LEGAL_SYNC_API_KEY` | Значение `API_KEY` из Legal Sync |

Оба Compose-файла задают `host.docker.internal:host-gateway`, поэтому схема
работает и в Docker Engine на Linux. Если сервисы на разных серверах, вместо
этого имени укажите доступный адрес соответствующего сервера. После изменения
настроек RAG пересоздайте его контейнер. Включите регистрацию в Legal Sync
до новой загрузки базы знаний в RAG.

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose ps
curl --fail http://localhost:8000/api/v1/health
```

Ожидаемый ответ: `{"status":"ok","database":"ok"}`. Админка доступна на
`http://localhost:8000/admin`; в ней видны реестр документов, очередь и ошибки
отправки. При другом `LEGAL_SYNC_PORT` используйте этот порт в адресах, включая
обратный адрес в RAG. При другом `API_V1_PREFIX` замените `/api/v1` в запросе;
проверка состояния контейнера учитывает этот параметр автоматически.

Миграции выполняются при старте (`RUN_MIGRATIONS_ON_START=true`).
`HYPERCORN_WORKERS` задаёт число процессов приложения. Мониторинг и обработка
работают по `MONITORING_CRON_HOUR`, `PROCESSING_CRON_HOUR` в зоне `TIMEZONE`;
параллельные обработчики используют блокировки PostgreSQL.

Для просмотра ошибок запуска:

```bash
docker compose logs --tail=100 legal_sync_service db
```

Обновление приложения: `git pull --ff-only`, затем повторите сборку и
`docker compose up -d --wait --wait-timeout 180`. Обычный `docker compose down`
сохраняет том PostgreSQL.
