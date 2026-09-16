from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.background_tasks import scheduler as scheduler_module
from app.schemas.configuration import ConfigurationValues


@pytest.mark.parametrize(
    ("hour", "now", "expected"),
    [
        ("*", "2026-09-16T10:18:00+03:00", "2026-09-16T11:00:00+03:00"),
        ("*", "2026-09-16T23:18:00+03:00", "2026-09-17T00:00:00+03:00"),
        ("3", "2026-09-16T10:18:00+03:00", "2026-09-17T03:00:00+03:00"),
    ],
)
def test_monitoring_schedule_and_disabled_delivery(hour, now, expected):
    scheduler = scheduler_module.create_scheduler(
        ConfigurationValues(monitoring_cron_hour=hour, timezone="Europe/Moscow"),
    )

    assert [job.id for job in scheduler.get_jobs()] == ["legal_sync_monitoring", "legal_sync_configuration"]
    trigger = scheduler.get_job("legal_sync_monitoring").trigger
    assert trigger.get_next_fire_time(None, datetime.fromisoformat(now)) == datetime.fromisoformat(expected)


def test_enabled_delivery_adds_processing_job():
    scheduler = scheduler_module.create_scheduler(
        ConfigurationValues(processing_cron_hour="4", timezone="Europe/Moscow", rag_delivery_enabled=True),
    )

    trigger = scheduler.get_job("legal_sync_processing").trigger
    assert trigger.get_next_fire_time(None, datetime.fromisoformat("2026-09-16T03:18:00+03:00")) == datetime.fromisoformat("2026-09-16T04:00:00+03:00")


async def test_disabled_processing_job_does_not_open_queue_or_http_client(monkeypatch):
    monkeypatch.setattr(
        scheduler_module, "get_settings",
        lambda: SimpleNamespace(rag=SimpleNamespace(rag_delivery_enabled=True)),
    )
    monkeypatch.setattr(scheduler_module, "load_configuration", AsyncMock(return_value=ConfigurationValues()))
    context = Mock(side_effect=AssertionError("Disabled job must not acquire resources"))
    monkeypatch.setattr(scheduler_module, "_job_context", context)

    await scheduler_module.run_processing_job()

    context.assert_not_called()


async def test_paused_monitoring_does_not_open_queue_or_http_client(monkeypatch):
    monkeypatch.setattr(scheduler_module, "load_configuration", AsyncMock(return_value=ConfigurationValues(monitoring_enabled=False)))
    context = Mock(side_effect=AssertionError("Paused monitoring must not acquire resources"))
    monkeypatch.setattr(scheduler_module, "_job_context", context)
    await scheduler_module.run_monitoring_job()
    context.assert_not_called()


def test_unchanged_configuration_preserves_trigger_and_timezone_change_reschedules():
    values = ConfigurationValues(monitoring_cron_hour="4", timezone="Europe/Moscow")
    scheduler = scheduler_module.create_scheduler(values)
    initial_trigger = scheduler.get_job("legal_sync_monitoring").trigger
    scheduler_module.apply_configuration(scheduler, values)
    assert scheduler.get_job("legal_sync_monitoring").trigger is initial_trigger
    scheduler_module.apply_configuration(scheduler, values.model_copy(update={"timezone": "Europe/Samara"}))
    updated_trigger = scheduler.get_job("legal_sync_monitoring").trigger
    assert updated_trigger is not initial_trigger
    assert updated_trigger.get_next_fire_time(None, datetime.fromisoformat("2026-09-16T03:18:00+04:00")) == datetime.fromisoformat("2026-09-16T04:00:00+04:00")
