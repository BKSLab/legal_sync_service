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
