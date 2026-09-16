# Legal Sync Service

Сервис мониторинга изменений нормативных актов и подготовки обновлений для RAG Service.

## Быстрый старт

1. Создать PostgreSQL-базу, например `legal_sync_service_local`.
2. Заполнить `.env` по образцу `.env.example`. Для запуска Python вне Docker
   указать `POSTGRES_HOST=localhost` и порт вашей PostgreSQL в `POSTGRES_PORT`.
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

Все значения окружения задаются в `.env`. Приложение получает их через
`env_file`; Compose не переопределяет адрес БД или RAG. Для Docker задайте
`POSTGRES_HOST=db`, `POSTGRES_PORT=5432`, `API_V1_PREFIX=/api/v1` и
`LEGAL_SYNC_PORT=8003`, как в `.env.example`. В секции `environment` PostgreSQL
остались только ссылки на значения из `.env`: образ ожидает `POSTGRES_DB`,
а приложение использует `POSTGRES_NAME`. Ключи API и пароль админки в контейнеры
PostgreSQL и nginx не передаются.

Внешние HTTP-запросы проходят по цепочке
`IP:LEGAL_SYNC_PORT → nginx:80 → legal_sync_service:8000 → db:5432`.
Только nginx публикует порт на хост; приложение и БД доступны внутри Docker-сети.
Сервис работает по HTTP через IP и порт, без домена и TLS. Для такого запуска
оставьте `ADMIN_SESSION_HTTPS_ONLY=false`.

Compose создаёт базу с именем `POSTGRES_NAME` и ждёт готовности PostgreSQL перед
запуском миграций. Затем nginx ждёт готовности приложения. Данные сохраняются в
томе `pg_legal_sync_data`.

Для Legal Sync и RAG на одном Docker-сервере используются опубликованные порты:

| Где задать | Переменная | Значение |
| --- | --- | --- |
| Legal Sync `.env` | `LEGAL_SYNC_PORT` | `8003` |
| Legal Sync `.env` | `RAG_SERVICE_BASE_URL` | `http://host.docker.internal:8002` |
| Legal Sync `.env` | `RAG_SERVICE_API_KEY` | Значение `API_KEY` из RAG |
| RAG `.env` | `LEGAL_SYNC_ENABLED` | `true` |
| RAG `.env` | `LEGAL_SYNC_BASE_URL` | `http://host.docker.internal:8003` |
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
docker compose exec nginx nginx -t
curl --fail http://localhost:8003/api/v1/health
```

Ожидаемый ответ: `{"status":"ok","database":"ok"}`. Админка доступна на
`http://<IP-сервера>:8003/admin`; в ней видны реестр документов, очередь и ошибки
отправки. При другом `LEGAL_SYNC_PORT` используйте этот порт в адресах, включая
обратный адрес в RAG. При другом `API_V1_PREFIX` замените `/api/v1` в запросе;
проверки состояния приложения и nginx учитывают этот параметр автоматически.
Проверка nginx проходит через приложение до БД.

Конфигурация прокси находится в `nginx/default.conf`. Nginx передаёт API,
админку и статику в приложение, сохраняет внешний адрес с портом при
перенаправлениях и обновляет адрес приложения через Docker DNS после
пересоздания его контейнера. Таймаут ожидания ответа — 600 секунд для ручного
мониторинга и обработки очереди; предел тела запроса — 10 МБ.

Миграции выполняются при старте (`RUN_MIGRATIONS_ON_START=true`).
`HYPERCORN_WORKERS` задаёт число процессов приложения. Мониторинг и обработка
работают по `MONITORING_CRON_HOUR`, `PROCESSING_CRON_HOUR` в зоне `TIMEZONE`;
параллельные обработчики используют блокировки PostgreSQL.

Для просмотра ошибок запуска:

```bash
docker compose logs --tail=100 nginx legal_sync_service db
```

Обновление приложения: `git pull --ff-only`, затем повторите сборку и
`docker compose up -d --wait --wait-timeout 180`. Обычный `docker compose down`
сохраняет том PostgreSQL.

После изменения `nginx/default.conf` проверьте конфигурацию командой
`docker compose exec nginx nginx -t` и примените её командой
`docker compose exec nginx nginx -s reload`.
