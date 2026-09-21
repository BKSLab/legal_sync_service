import asyncio
import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.clients.rag import RagClient
from app.core.settings import RagSettings
from app.db.models.delivery_attempt import DeliveryAttempt
from app.exceptions.rag import RagClientError
from app.repositories.delivery_journal import DeliveryJournal
from app.repositories.legal_changes import LegalChangesRepository
from app.services.processing import ProcessingService
from sqlalchemy import select
from tests.integration.test_admin import seed_admin_data
from tests.unit.services.test_processing_service import _change

TEXT = 'Статья 59. Текст новой редакции.'


def receipt(document_id='labor_code_rf', **overrides):
    result = dict(
        operation_id=str(uuid4()), document_id=document_id, section_number='59', version='2027-03-01',
        input_sha256=hashlib.sha256(TEXT.encode()).hexdigest(), integrity_verified=True,
        status='succeeded', chunks_count=2, superseded_chunks=3, collection_name='test_collection', warnings=[],
    )
    result.update(overrides)
    return result


def processor(factory, client):
    service = ProcessingService(
        legal_changes_repository=AsyncMock(spec=LegalChangesRepository), pravo_ebpi_client=AsyncMock(),
        rag_client=RagClient(client, RagSettings(_env_file=None, rag_service_api_key='test-key')),
        delivery_enabled=True, journal=DeliveryJournal(factory),
    )
    service.section_text.get_text = AsyncMock(return_value=TEXT)
    service.section_text.source_reference = lambda **kwargs: 'test-redaction/495396'
    return service


async def attempts(factory):
    async with factory() as session:
        return (await session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.started_at))).all()


async def test_receipt_correlates_sent_text_http_request_and_collection(session_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=receipt())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await processor(session_factory, client)._process_change(_change(), {})
    row, = await attempts(session_factory)
    assert row.id == requests[0].headers['X-Request-ID']
    assert requests[0].headers['X-Legal-Sync-Change-ID'] == '10'
    assert row.status == 'succeeded' and row.stage == 'saved'
    assert row.details['input_sha256'] == row.details['response']['input_sha256']
    assert row.details['response']['integrity_verified'] is True
    assert row.details['http_status'] == 200
    assert TEXT not in json.dumps(row.details)


async def test_timeout_is_unknown_and_retry_keeps_both_attempts(session_factory):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout('reply lost', request=request)
        return httpx.Response(200, json=receipt())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = processor(session_factory, client)
        with pytest.raises(RagClientError):
            await service._process_change(_change(), {})
        await service._process_change(_change(), {})
    first, second = await attempts(session_factory)
    assert first.status == 'unknown' and second.status == 'succeeded'
    assert first.id != second.id and first.change_id == second.change_id


@pytest.mark.parametrize('payload, expected', [
    ({'chunks_count': 2}, 'accepted'),
    (receipt(status='warning', warnings=['Registry write failed']), 'warning'),
])
async def test_legacy_and_warning_responses_are_not_plain_success(session_factory, payload, expected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        await processor(session_factory, client)._process_change(_change(), {})
    row, = await attempts(session_factory)
    assert row.status == expected


async def test_wrong_checksum_is_not_acknowledged_as_delivered(session_factory):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=receipt(input_sha256='wrong')))) as client:
        with pytest.raises(RagClientError, match='не соответствует'):
            await processor(session_factory, client)._process_change(_change(), {})
    row, = await attempts(session_factory)
    assert row.status == 'failed' and row.details['response']['input_sha256'] == 'wrong'


async def test_cancelled_delivery_survives_and_recovery_does_not_invent_success(session_factory):
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(processor(session_factory, client)._process_change(_change(), {}))
        try:
            await asyncio.wait_for(entered.wait(), 10)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    row, = await attempts(session_factory)
    assert row.status == 'unknown' and row.finished_at is not None
    async with session_factory() as session, session.begin():
        row = await session.get(DeliveryAttempt, row.id)
        row.status, row.finished_at = 'running', None
    await DeliveryJournal(session_factory).recover_interrupted()
    row, = await attempts(session_factory)
    assert row.status == 'unknown' and 'RAG' in row.error


async def test_trace_page_is_protected_and_handles_unavailable_rag(admin_client, session_factory, monkeypatch):
    read_rag = AsyncMock(side_effect=RagClientError('История RAG недоступна: HTTP 404.'))
    monkeypatch.setattr(RagClient, 'get_ingestion_runs', read_rag)
    await seed_admin_data(session_factory)
    assert (await admin_client.get('/admin/document-trace?document_id=tk-197-2001')).status_code == 302
    read_rag.assert_not_awaited()
    await admin_client.post('/admin/login', data={'username': 'operator', 'password': 'test-admin-password'})
    response = await admin_client.get('/admin/document-trace?document_id=tk-197-2001')
    assert response.status_code == 200
    assert 'Постановка на контроль' in response.text and 'Изменения, проверка и очередь' in response.text
    assert 'HTTP 404' in response.text and 'Трудовой кодекс' in response.text
    assert (await admin_client.get('/admin/document-trace?attempts_page=0')).status_code == 400


async def test_trace_page_renders_live_rag_stages_and_escapes_content(admin_client, session_factory, monkeypatch):
    await seed_admin_data(session_factory)
    monkeypatch.setattr(RagClient, 'get_ingestion_runs', AsyncMock(return_value={
        'total': 1, 'items': [{
            'id': str(uuid4()), 'kind': 'section', 'section_number': '59', 'display_status': 'succeeded',
            'version': '2027-03-01', 'collection_name': 'test_collection', 'duration_seconds': 3,
            'request_id': 'attempt', 'error': '<script>alert(1)</script>', 'stages': [], 'result': {},
        }],
    }))
    await admin_client.post('/admin/login', data={'username': 'operator', 'password': 'test-admin-password'})
    response = await admin_client.get('/admin/document-trace?document_id=tk-197-2001')
    assert response.status_code == 200
    assert 'test_collection' in response.text and 'Коллекция обновлена' in response.text
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;' in response.text
