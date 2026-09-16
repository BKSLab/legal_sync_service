import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.admin.dashboard import get_dashboard_stats
from app.clients.pravo_ebpi import PravoEbpiClient
from app.core.settings import PravoEbpiSettings
from app.db.models import (
    LegalChange,
    MonitoringDocumentCheck,
    MonitoringLogEntry,
    MonitoringRun,
    TrackedDocument,
)
from app.exceptions.monitoring import MonitoringJournalError
from app.exceptions.pravo_ebpi import PravoEbpiRequestError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.monitoring import MonitoringJournal
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.monitoring import MonitoringDocumentResult
from app.services.monitoring import MonitoringService
from bs4 import BeautifulSoup
from sqlalchemy import delete, select, text
from tests.unit.services.test_monitoring_service import (
    _build_service,
    _redaction,
    _tracked_document,
)


@pytest.fixture
async def monitored_document(session_factory):
    values = vars(_tracked_document())
    values.update(audience="both", topics=[], source_title="Трудовой кодекс", publication_block="president", is_active=True)
    async with session_factory() as session:
        session.add(TrackedDocument(**values))
        await session.commit()
    return _tracked_document()


def journal_service(session_factory, document=None, redactions=None, html="", content=None):
    service, tracked, changes, client = _build_service(html, content or [], redactions or [], document)
    if document is None:
        tracked.get_all_active.return_value = []
    service.journal = MonitoringJournal(session_factory)
    return service, tracked, changes, client


async def read_run(session_factory, run_id):
    async with session_factory() as session:
        run = await session.get(MonitoringRun, run_id)
        checks = (await session.scalars(select(MonitoringDocumentCheck).where(MonitoringDocumentCheck.run_id == run_id).order_by(MonitoringDocumentCheck.id))).all()
        entries = (await session.scalars(select(MonitoringLogEntry).where(MonitoringLogEntry.run_id == run_id).order_by(MonitoringLogEntry.id))).all()
    return run, checks, entries


@pytest.mark.parametrize("with_document", [False, True])
async def test_empty_and_unchanged_runs_are_durable(session_factory, monitored_document, with_document):
    service, _, _, _ = journal_service(session_factory, monitored_document if with_document else None)
    result = await service.run_monitoring()
    run, checks, entries = await read_run(session_factory, result.run_id)
    assert run.source == "manual"
    assert run.status == "succeeded"
    assert run.finished_at >= run.started_at
    assert run.documents_total == run.documents_checked == int(with_document)
    assert run.changes_created == run.documents_failed == 0
    assert len(checks) == int(with_document)
    assert entries[0].stage == "started" and entries[-1].stage == "finished"
    if with_document:
        assert checks[0].status == "succeeded"
        assert "redactions_selected" in [entry.stage for entry in entries]


async def test_live_progress_survives_working_session_rollback_and_concurrent_attempt_is_skipped(session_factory, monitored_document):
    started, release = asyncio.Event(), asyncio.Event()
    service, _, _, client = journal_service(session_factory, monitored_document)

    async def get_redactions(**kwargs):
        started.set()
        await release.wait()
        return []

    client.get_redactions.side_effect = get_redactions
    running = asyncio.create_task(service.run_monitoring())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with session_factory() as session:
            run = await session.scalar(select(MonitoringRun).where(MonitoringRun.status == "running"))
            assert run.documents_total == 1
            run_id = run.id
            await session.rollback()
        # Внешняя сессия видит этап до завершения HTTP-запроса.
        active, checks, entries = await read_run(session_factory, run_id)
        assert checks[0].status == "running" and entries[-1].stage == "document_resolved"
        await MonitoringJournal(session_factory).recover_abandoned()
        assert (await read_run(session_factory, run_id))[0].status == "running"
        second, tracked, _, second_client = journal_service(session_factory, monitored_document)
        result = await second.run_monitoring(source="scheduled")
        assert result.already_running
        tracked.get_all_active.assert_not_awaited()
        second_client.get_redactions.assert_not_awaited()
        assert (await read_run(session_factory, result.run_id))[0].status == "skipped"
        async with session_factory() as session:
            stats = await get_dashboard_stats(session)
        assert stats.last_monitoring.id == run_id
    finally:
        release.set()
        await asyncio.wait_for(running, 5)


async def test_scheduled_workers_do_not_repeat_a_completed_slot(session_factory):
    first, _, _, _ = journal_service(session_factory)
    second, second_repo, _, _ = journal_service(session_factory)
    one = await first.run_monitoring(source="scheduled")
    two = await second.run_monitoring(source="scheduled")
    assert not one.already_running and two.already_running
    second_repo.get_all_active.assert_not_awaited()
    assert "расписания" in (await read_run(session_factory, two.run_id))[0].error
    # Ручной запуск не привязан к минуте расписания.
    assert not (await second.run_monitoring()).already_running


async def test_incomplete_and_unrecognized_redactions_are_warnings(session_factory, monitored_document):
    service, _, _, _ = journal_service(session_factory, monitored_document, [
        _redaction(redid=100, redcompleted=False), _redaction(redid=101, redcaption="Редакция без ссылки на акт"),
    ])
    result = await service.run_monitoring()
    run, checks, entries = await read_run(session_factory, result.run_id)
    assert run.status == checks[0].status == "warnings"
    assert run.warnings_count == checks[0].warnings_count == 2
    assert checks[0].redactions_skipped_incomplete == 1
    assert {entry.stage for entry in entries if entry.level == "warning"} == {"redaction_incomplete", "missing_citation"}


async def test_partial_changes_and_document_error_are_not_lost(session_factory, monitored_document, redaction_html, redaction_content_nodes):
    service, _, _, client = journal_service(session_factory, monitored_document, [
        _redaction(), _redaction(redid=500001, reddate="20280101", redcaption="200. на 01.01.2028 (№ 246-ФЗ от 26.07.2026)"),
    ], redaction_html, redaction_content_nodes)
    client.get_redaction_text.side_effect = [redaction_html, PravoEbpiRequestError("HTTP 503")]
    result = await service.run_monitoring()
    run, checks, entries = await read_run(session_factory, result.run_id)
    assert run.status == checks[0].status == "failed"
    assert result.changes_created == checks[0].changes_created == run.changes_created == 3
    assert result.documents_failed == run.documents_failed == 1
    assert "HTTP 503" in checks[0].error and "PravoEbpiRequestError" in checks[0].error_traceback
    saved = next(entry for entry in entries if entry.stage == "changes_saved")
    assert saved.details["sections"] == ["57", "58", "59"]
    assert entries[-1].stage == "finished"


async def test_real_repository_changes_match_journal_and_repeated_check_is_empty(
    session_factory, monitored_document, redaction_html, redaction_content_nodes,
):
    _, _, _, client = journal_service(session_factory, monitored_document, [_redaction()], redaction_html, redaction_content_nodes)
    async with session_factory() as session:
        service = MonitoringService(
            TrackedDocumentsRepository(session), LegalChangesRepository(session), client, MonitoringJournal(session_factory),
        )
        first = await service.run_monitoring()
        second = await service.run_monitoring()
        changes = (await session.scalars(select(LegalChange))).all()
    assert len(changes) == first.changes_created == 3 and second.changes_created == 0
    assert (await read_run(session_factory, first.run_id))[0].changes_created == len(changes)
    assert (await read_run(session_factory, second.run_id))[0].status == "succeeded"
    entries = (await read_run(session_factory, second.run_id))[2]
    assert next(entry for entry in entries if entry.stage == "redactions_filtered").details["already_known"] == [_redaction().redaction_id]


async def test_failure_of_one_document_does_not_hide_results_of_the_next(session_factory, monitored_document):
    other = _tracked_document(id=2, document_id="second-law", short_name="Другой закон")
    async with session_factory() as session:
        session.add(TrackedDocument(**{
            **vars(other), "audience": "both", "topics": [], "source_title": "Другой закон", "publication_block": "president", "is_active": True,
        }))
        await session.commit()
    service, tracked, _, client = journal_service(session_factory, monitored_document)
    tracked.get_all_active.return_value = [monitored_document, other]
    client.get_redactions.side_effect = [PravoEbpiRequestError("Недоступен первый акт"), []]
    result = await service.run_monitoring()
    run, checks, _ = await read_run(session_factory, result.run_id)
    assert run.documents_checked == run.documents_total == 2 and run.documents_failed == 1
    assert [check.status for check in checks] == ["failed", "succeeded"]


@pytest.mark.parametrize("failure_point", ["selection", "document"])
async def test_unexpected_errors_always_close_the_run(session_factory, monitored_document, failure_point):
    service, tracked, _, client = journal_service(session_factory, monitored_document)
    getattr(tracked if failure_point == "selection" else client, "get_all_active" if failure_point == "selection" else "get_redactions").side_effect = RuntimeError("Неожиданный сбой")
    with pytest.raises(RuntimeError, match="Неожиданный сбой"):
        await service.run_monitoring()
    run, checks, entries = await read_run(session_factory, 1)
    assert run.status == "failed" and run.finished_at
    assert "RuntimeError" in run.error_traceback
    assert entries[-1].stage == "failed"
    if checks:
        assert checks[0].status == "failed"


async def test_cancelled_run_and_document_are_interrupted(session_factory, monitored_document):
    service, _, _, client = journal_service(session_factory, monitored_document)
    started = asyncio.Event()

    async def block(**kwargs):
        started.set()
        await asyncio.Event().wait()

    client.get_redactions.side_effect = block
    task = asyncio.create_task(service.run_monitoring())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run, checks, entries = await read_run(session_factory, 1)
    assert run.status == checks[0].status == "interrupted"
    assert run.finished_at and entries[-1].stage == "interrupted"
    client.get_redactions.side_effect = None
    client.get_redactions.return_value = []
    assert not (await service.run_monitoring()).already_running


async def test_crashed_run_is_recovered_without_inventing_end_time(session_factory, monitored_document):
    # Запись running без живой блокировки моделирует SIGKILL / перезапуск контейнера.
    async with MonitoringJournal(session_factory).start("manual") as recorder:
        await recorder.set_total(1)
        await recorder.begin_document(monitored_document, MonitoringDocumentResult(document_id=monitored_document.document_id))
        await recorder.event("redaction_loading", "Загружается редакция", {"redaction_id": 123})
        run_id = recorder.run_id
    before, _, _ = await read_run(session_factory, run_id)
    await MonitoringJournal(session_factory).recover_abandoned()
    run, checks, entries = await read_run(session_factory, run_id)
    assert run.status == checks[0].status == "interrupted"
    assert run.finished_at is None and run.updated_at == before.updated_at
    assert entries[-2].stage == "redaction_loading" and entries[-1].stage == "interrupted"
    await MonitoringJournal(session_factory).recover_abandoned()
    assert len((await read_run(session_factory, run_id))[2]) == len(entries)


async def test_http_retries_are_visible_with_timing_and_without_headers(session_factory, monitored_document):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503) if attempts == 1 else httpx.Response(200, json={"redactions": []})

    service, _, _, _ = journal_service(session_factory, monitored_document)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Authorization": "secret-test-token"}) as client:
        service.pravo_ebpi_client = PravoEbpiClient(client, PravoEbpiSettings(_env_file=None, pravo_ebpi_retry_delay_seconds=0))
        result = await service.run_monitoring()
    run, _, entries = await read_run(session_factory, result.run_id)
    assert run.status == "warnings" and attempts == 2
    responses = [entry for entry in entries if entry.stage == "http_response"]
    assert [entry.details["status_code"] for entry in responses] == [503, 200]
    assert all(entry.details["duration_ms"] >= 0 for entry in responses)
    assert any(entry.stage == "http_retry" for entry in entries)
    assert "secret-test-token" not in json.dumps([entry.details for entry in entries])


async def test_journal_unavailable_prevents_unrecorded_work(session_factory):
    service, tracked, _, client = journal_service(session_factory)
    async with session_factory() as session:
        await session.execute(text("ALTER TABLE monitoring_runs RENAME TO unavailable_monitoring_runs"))
        await session.commit()
    try:
        with pytest.raises(MonitoringJournalError):
            await service.run_monitoring()
        tracked.get_all_active.assert_not_awaited()
        client.get_redactions.assert_not_awaited()
    finally:
        async with session_factory() as session:
            await session.execute(text("ALTER TABLE unavailable_monitoring_runs RENAME TO monitoring_runs"))
            await session.commit()


async def test_history_survives_document_removal(session_factory, monitored_document):
    service, _, _, _ = journal_service(session_factory, monitored_document)
    result = await service.run_monitoring()
    async with session_factory() as session:
        await session.execute(delete(TrackedDocument))
        await session.commit()
    _, checks, _ = await read_run(session_factory, result.run_id)
    assert checks[0].tracked_document_id is None
    assert checks[0].document_title == monitored_document.short_name


async def test_admin_journal_filters_details_export_and_access(admin_client, session_factory, monitored_document):
    service, _, _, client = journal_service(session_factory, monitored_document)
    client.get_redactions.side_effect = PravoEbpiRequestError("<script>alert('portal')</script>")
    result = await service.run_monitoring()
    path = f"/admin/monitoring-log/{result.run_id}"
    for url in ("/admin/monitoring-log", path, path + "/export"):
        assert (await admin_client.get(url)).status_code == 302
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    dashboard = await admin_client.get("/admin/dashboard")
    assert "Последняя проверка документов" in dashboard.text and "Запуск №1" in dashboard.text
    page = await admin_client.get("/admin/monitoring-log?status=failed&source=manual&document=Трудовой")
    assert page.status_code == 200 and "Открыть запуск" in page.text
    menu = BeautifulSoup(page.text, "html.parser").find("a", href="http://test/admin/monitoring-log")
    assert menu and "Журнал мониторинга" in menu.text
    empty = await admin_client.get("/admin/monitoring-log?status=succeeded")
    assert "Запусков по выбранным условиям пока нет." in empty.text
    details = await admin_client.get(path)
    assert details.status_code == 200, details.text
    assert "&lt;script&gt;" in details.text and "<script>alert('portal')</script>" not in details.text
    assert "Подробности ошибки документа" in details.text
    export = await admin_client.get(path + "/export")
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="monitoring-1.json"'
    data = export.json()
    assert data["run"]["documents_failed"] == 1 and data["documents"][0]["error_traceback"]
    assert data["entries"][-1]["stage"] == "finished"
    assert (await admin_client.get(path + "?check=1&level=error")).status_code == 200
    assert (await admin_client.get("/admin/monitoring-log/999")).status_code == 404
    for invalid in ("?status=other", "?page=0", "?date_from=not-a-date"):
        assert (await admin_client.get("/admin/monitoring-log" + invalid)).status_code == 400
    assert (await admin_client.post(path)).status_code == 405


async def test_admin_pagination_does_not_truncate_export(admin_client, session_factory):
    service, _, _, _ = journal_service(session_factory)
    result = await service.run_monitoring()
    async with session_factory() as session:
        session.add_all([MonitoringLogEntry(run_id=result.run_id, created_at=datetime.now(UTC), level="info", stage="extra", message=f"Этап {index}") for index in range(105)])
        await session.commit()
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    path = f"/admin/monitoring-log/{result.run_id}"
    first = await admin_client.get(path)
    second = await admin_client.get(path + "?page=2")
    assert len(BeautifulSoup(first.text, "html.parser").select(".monitoring-entry")) == 100
    assert "Этап 104" not in first.text and "Этап 104" in second.text
    assert len((await admin_client.get(path + "/export")).json()["entries"]) == 108
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    assert "Запусков по выбранным условиям пока нет." in (await admin_client.get("/admin/monitoring-log?date_from=" + tomorrow)).text
