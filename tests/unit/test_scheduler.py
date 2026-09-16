from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.background_tasks import scheduler as scheduler_module
from app.core.settings import SchedulerSettings


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
        SchedulerSettings(_env_file=None, monitoring_cron_hour=hour, timezone="Europe/Moscow"),
    )

    assert [job.id for job in scheduler.get_jobs()] == ["legal_sync_monitoring"]
    trigger = scheduler.get_job("legal_sync_monitoring").trigger
    assert trigger.get_next_fire_time(None, datetime.fromisoformat(now)) == datetime.fromisoformat(expected)


def test_enabled_delivery_adds_processing_job():
    scheduler = scheduler_module.create_scheduler(
        SchedulerSettings(_env_file=None, processing_cron_hour=4, timezone="Europe/Moscow"),
        rag_delivery_enabled=True,
    )

    trigger = scheduler.get_job("legal_sync_processing").trigger
    assert trigger.get_next_fire_time(None, datetime.fromisoformat("2026-09-16T03:18:00+03:00")) == datetime.fromisoformat("2026-09-16T04:00:00+03:00")


async def test_disabled_processing_job_does_not_open_database_or_http_client(monkeypatch):
    monkeypatch.setattr(
        scheduler_module, "get_settings",
        lambda: SimpleNamespace(rag=SimpleNamespace(rag_delivery_enabled=False)),
    )
    context = Mock(side_effect=AssertionError("Disabled job must not acquire resources"))
    monkeypatch.setattr(scheduler_module, "_job_context", context)

    await scheduler_module.run_processing_job()

    context.assert_not_called()
