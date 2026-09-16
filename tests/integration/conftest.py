from types import SimpleNamespace

import pytest
from app.admin import create_admin
from app.core.settings import AdminSettings, PravoEbpiSettings, RagSettings, SchedulerSettings
from app.db.models import Base
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container():
    """Изолированная PostgreSQL; настройки БД приложения не используются."""
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
async def session_factory(postgres_container):
    engine = create_async_engine(postgres_container.get_connection_url().replace("psycopg2", "asyncpg"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def admin_client(session_factory, monkeypatch):
    settings = SimpleNamespace(
        admin=AdminSettings(
            _env_file=None, secret_key="admin-test-session-secret",
            admin_login="operator", admin_password="test-admin-password",
        ),
        rag=RagSettings(_env_file=None, rag_delivery_enabled=False, rag_service_api_key="test-rag-key"),
        scheduler=SchedulerSettings(_env_file=None),
        pravo_ebpi=PravoEbpiSettings(_env_file=None),
    )
    monkeypatch.setattr("app.admin.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.views.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.preview.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.configuration.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.configuration.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.configuration.async_session_factory", session_factory)
    app = FastAPI()
    create_admin(app, session_factory.kw["bind"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
