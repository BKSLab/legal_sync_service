import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from app.api.v1.endpoints.monitoring import router as monitoring_router
from app.background_tasks import scheduler as scheduler_module
from app.db.models import ConfigurationChange, ServiceConfiguration
from app.dependencies.auth import verify_api_key
from app.dependencies.clients import get_pravo_ebpi_client, get_rag_client
from app.dependencies.db_session import get_db_session
from app.exceptions.configuration import ConfigurationConflictError
from app.repositories.configuration import ConfigurationRepository
from app.schemas.configuration import ConfigurationValues
from app.services.configuration import load_configuration
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.test_admin import _form_values


async def _login(admin_client):
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    return await admin_client.get("/admin/configuration")


async def test_concurrent_initialization_and_saved_values_survive_new_workers(session_factory):
    initial = ConfigurationValues(rag_delivery_enabled=True, monitoring_cron_hour="3")

    async def read(defaults):
        async with session_factory() as session:
            return await ConfigurationRepository(session).get_or_create(defaults)

    first, second = await asyncio.gather(read(initial), read(initial))
    assert first.version == second.version == 1
    assert first.rag_delivery_enabled
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ServiceConfiguration)) == 1
        await ConfigurationRepository(session).save(ConfigurationValues(processing_max_retries=8), 1, "operator")
    current = await read(initial)
    assert not current.rag_delivery_enabled
    assert current.processing_max_retries == 8
    assert current.monitoring_cron_hour == "*"
    assert current.version == 2


async def test_concurrent_edits_keep_one_complete_configuration_and_one_audit_entry(session_factory):
    async with session_factory() as session:
        await ConfigurationRepository(session).get_or_create(ConfigurationValues())

    async def save(retries, actor):
        async with session_factory() as session:
            return await ConfigurationRepository(session).save(
                ConfigurationValues(processing_max_retries=retries), 1, actor,
            )

    results = await asyncio.gather(save(5, "first"), save(7, "second"), return_exceptions=True)
    assert sum(isinstance(value, ConfigurationConflictError) for value in results) == 1
    async with session_factory() as session:
        current = await ConfigurationRepository(session).get_or_create(ConfigurationValues())
        history = await ConfigurationRepository(session).history()
        assert current.version == 2 and len(history) == 1
        assert history[0].changed_by == current.updated_by
        assert history[0].changes == {"processing_max_retries": {"before": 3, "after": current.processing_max_retries}}


async def test_configuration_requires_login_and_csrf(admin_client, session_factory):
    for method in (admin_client.get, admin_client.post):
        response = await method("/admin/configuration")
        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")
    page = await _login(admin_client)
    form = _form_values(page.text)
    for token in ("", "wrong", "неверный токен"):
        response = await admin_client.post("/admin/configuration", data={**form, "csrf_token": token, "rag_delivery_enabled": "on"})
        assert response.status_code == 403
    async with session_factory() as session:
        assert not (await session.get(ServiceConfiguration, 1)).rag_delivery_enabled
        assert await session.scalar(select(func.count()).select_from(ConfigurationChange)) == 0


async def test_admin_saves_configuration_shows_live_state_and_rejects_stale_form(admin_client, session_factory):
    page = await _login(admin_client)
    assert page.status_code == 200
    assert "test-rag-key" not in page.text
    assert "test-admin-password" not in page.text
    form = _form_values(page.text)
    assert "rag_delivery_enabled" not in form
    assert form["monitoring_enabled"] == "on"
    form.update(rag_delivery_enabled="on", monitoring_cron_hour="*/3", processing_cron_hour="*", timezone="Europe/Samara", processing_max_retries="7")
    form.pop("monitoring_enabled")
    response = await admin_client.post("/admin/configuration", data=form)
    assert response.status_code == 303
    saved_page = await admin_client.get(response.headers["location"])
    assert "Конфигурация сохранена" in saved_page.text
    assert "Каждые 3 часа" in saved_page.text
    saved = _form_values(saved_page.text)
    assert saved["version"] == "2"
    assert saved["rag_delivery_enabled"] == "on"
    assert "monitoring_enabled" not in saved
    dashboard = await admin_client.get("/admin/dashboard")
    assert "Отправка в RAG включена" in dashboard.text
    assert "Автоматический мониторинг приостановлен" in dashboard.text
    conflict = await admin_client.post("/admin/configuration", data=form)
    assert conflict.status_code == 409
    assert "Загрузить актуальные настройки" in conflict.text
    async with session_factory() as session:
        history = await ConfigurationRepository(session).history()
        assert len(history) == 1
        assert history[0].changes["rag_delivery_enabled"] == {"before": False, "after": True}
        assert history[0].changed_by == "operator"
    # Повтор без изменений не создаёт фиктивную новую версию и запись истории.
    assert (await admin_client.post("/admin/configuration", data=saved)).status_code == 303
    assert (await load_configuration()).version == 2


@pytest.mark.parametrize("field,value,message", [
    ("processing_max_retries", "0", "целое число от 1 до 20"),
    ("processing_max_retries", "21", "целое число от 1 до 20"),
    ("monitoring_cron_hour", "25", "корректное расписание"),
    ("processing_cron_hour", "oops", "корректное расписание"),
    ("timezone", "Mars/Olympus", "существующий часовой пояс"),
])
async def test_invalid_configuration_is_not_saved(admin_client, field, value, message):
    form = _form_values((await _login(admin_client)).text)
    form[field] = value
    response = await admin_client.post("/admin/configuration", data=form)
    assert response.status_code == 422
    assert message in response.text
    assert (await load_configuration()).version == 1


async def test_enable_delivery_requires_key_without_disclosing_it(admin_client):
    from app.admin import configuration as view_module

    settings = view_module.get_settings()
    settings.rag = settings.rag.model_copy(update={"rag_service_api_key": None})
    page = await _login(admin_client)
    form = _form_values(page.text)
    form["rag_delivery_enabled"] = "on"
    response = await admin_client.post("/admin/configuration", data=form)
    assert response.status_code == 422
    assert "задайте RAG_SERVICE_API_KEY" in response.text
    assert not (await load_configuration()).rag_delivery_enabled


async def test_manual_processing_uses_configuration_saved_in_admin(admin_client, session_factory):
    form = _form_values((await _login(admin_client)).text)
    app = FastAPI()
    app.include_router(monitoring_router)
    rag_client, pravo_client = AsyncMock(), AsyncMock()

    async def db_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[get_rag_client] = lambda: rag_client
    app.dependency_overrides[get_pravo_ebpi_client] = lambda: pravo_client
    app.dependency_overrides[verify_api_key] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for enabled in (False, True, False):
            if enabled:
                form["rag_delivery_enabled"] = "on"
            else:
                form.pop("rag_delivery_enabled", None)
            assert (await admin_client.post("/admin/configuration", data=form)).status_code == 303
            form = _form_values((await admin_client.get("/admin/configuration")).text)
            result = await client.post("/monitoring/process")
            assert result.status_code == 200
            assert result.json()["delivery_disabled"] is (not enabled)
            assert result.json()["changes_selected"] == 0
    assert rag_client.mock_calls == pravo_client.mock_calls == []


async def test_two_schedulers_reload_database_settings_without_restart(admin_client, monkeypatch):
    await _login(admin_client)
    monkeypatch.setattr(scheduler_module, "load_configuration", load_configuration)
    first = scheduler_module.create_scheduler(await load_configuration())
    second = scheduler_module.create_scheduler(await load_configuration())
    form = _form_values((await admin_client.get("/admin/configuration")).text)
    form.update(rag_delivery_enabled="on", processing_cron_hour="4", timezone="Europe/Samara")
    form.pop("monitoring_enabled")
    assert (await admin_client.post("/admin/configuration", data=form)).status_code == 303
    for worker in (first, second):
        await worker.get_job("legal_sync_configuration").func()
        assert worker.get_job("legal_sync_monitoring") is None
        delivery = worker.get_job("legal_sync_processing")
        assert delivery.trigger.get_next_fire_time(None, datetime.fromisoformat("2026-09-16T03:10:00+04:00")) == datetime.fromisoformat("2026-09-16T04:00:00+04:00")
    form = _form_values((await admin_client.get("/admin/configuration")).text)
    form.pop("rag_delivery_enabled")
    assert (await admin_client.post("/admin/configuration", data=form)).status_code == 303
    for worker in (first, second):
        await worker.get_job("legal_sync_configuration").func()
        assert [job.id for job in worker.get_jobs()] == ["legal_sync_configuration"]
