import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from app.clients.rag import RagClient
from app.clients.verification import VerificationUnavailable
from app.core.settings import RagSettings
from app.db.models import (
    AutomationCase,
    AutomationRun,
    DeliveryAttempt,
    LegalChange,
    LegalChangeStatus,
    TrackedDocument,
)
from app.repositories.automation import AutomationRepository
from app.repositories.configuration import ConfigurationRepository
from app.repositories.delivery_journal import DeliveryJournal
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.configuration import ConfigurationValues
from app.services.automation import AutomationService
from app.services.processing import ProcessingService
from app.services.verification import digest, json_digest, payload_identity
from bs4 import BeautifulSoup
from sqlalchemy import select
from tests.automation_fixtures import DOC_HASH, LAW_HASH, pravo, report


async def setup(session_factory, mode="auto", verifier=None):
    values = ConfigurationValues(automation_mode=mode, rag_delivery_enabled=True, verification_model="test-model")
    async with session_factory() as session:
        await ConfigurationRepository(session).get_or_create(values)
        document = TrackedDocument(document_id="test-code", short_name="Тестовый кодекс", full_title="Тестовый кодекс",
                                   category="labor_code", audience="both", topics=[], source_title="Тестовый кодекс",
                                   publication_block="president", document_number="197-ФЗ", adoption_date=date(2001,12,30),
                                   monitor_from=date(2026,1,1), is_active=True, ebpi_doc_hash=DOC_HASH)
        session.add(document)
        await session.flush()
        for number in ["57","59"]:
            session.add(LegalChange(tracked_document_id=document.id, section_number=number, section_title="Условия",
                                    amending_law_ref="от 26.07.2026 № 246-ФЗ", amending_doc_hash=LAW_HASH,
                                    amending_act_type="Федеральный закон", amending_act_number="246-ФЗ", amending_act_date=date(2026,7,26),
                                    ebpi_redaction_id=2, redaction_date=date(2027,3,1), effective_date=date(2027,3,1),
                                    send_at=datetime(2027,3,1,tzinfo=UTC), status=LegalChangeStatus.DRAFT))
        await session.commit()
    async def configuration():
        async with session_factory() as session:
            return await ConfigurationRepository(session).get_or_create(values)
    reviewer = verifier or AsyncMock()
    if verifier is None:
        reviewer.review.return_value = (report(), {"usage":{"total_tokens":123}})
    repository = AutomationRepository(session_factory)
    return AutomationService(repository, pravo(), reviewer, configuration), configuration


async def records(factory):
    async with factory() as session:
        changes = [await LegalChangesRepository(session).get_by_id(i) for i in (1,2)]
        cases = list(await session.scalars(select(AutomationCase)))
        runs = list(await session.scalars(select(AutomationRun).order_by(AutomationRun.id)))
        return changes, cases, runs


async def test_whole_law_is_atomically_approved_and_sealed(session_factory):
    service, _ = await setup(session_factory)
    assert (await service.run())["processed"] == 1
    changes, cases, runs = await records(session_factory)
    assert cases[0].status == runs[0].status == "approved"
    for change in changes:
        assert change.status == LegalChangeStatus.SCHEDULED
        assert change.review_origin == "auto" and change.automation_run_id == runs[0].id
        assert change.verified_text_sha256 == digest(change.consolidated_text)
        assert change.verified_payload_sha256 == json_digest(payload_identity(change))
    assert (await service.run())["processed"] == 0
    assert service.verifier.review.await_count == 1


async def test_shadow_pass_does_not_schedule_and_enabling_auto_rechecks(session_factory):
    service, configuration = await setup(session_factory, mode="shadow")
    await service.run()
    changes, cases, _ = await records(session_factory)
    assert cases[0].status == "shadow_pass" and all(c.status == LegalChangeStatus.DRAFT for c in changes)
    async with session_factory() as session:
        current = await configuration()
        await ConfigurationRepository(session).save(ConfigurationValues(**{**current.model_dump(exclude={'version','updated_at','updated_by'}),'automation_mode':'auto'}), current.version, "operator")
    await service.run()
    changes, _, runs = await records(session_factory)
    assert all(c.status == LegalChangeStatus.SCHEDULED for c in changes) and len(runs) == 2


@pytest.mark.parametrize("mutation", ["date", "new_event", "configuration", "human_reject"])
async def test_concurrent_changes_prevent_any_partial_approval(session_factory, mutation):
    reviewer = AsyncMock()
    service, configuration = await setup(session_factory, verifier=reviewer)
    async def review(snapshot, model):
        async with session_factory() as session:
            if mutation == "configuration":
                current = await configuration()
                await ConfigurationRepository(session).save(ConfigurationValues(), current.version, "operator")
            elif mutation == "new_event":
                original = await session.get(LegalChange,1)
                session.add(LegalChange(tracked_document_id=original.tracked_document_id, section_number="60",
                                       amending_law_ref=original.amending_law_ref, ebpi_redaction_id=2, status=LegalChangeStatus.DRAFT))
                await session.commit()
            else:
                change = await session.get(LegalChange,1)
                if mutation == "date":
                    change.effective_date = date(2028,1,1)
                else:
                    change.status = LegalChangeStatus.CANCELLED
                await session.commit()
        return report(), {}
    reviewer.review.side_effect = review
    await service.run()
    changes, cases, runs = await records(session_factory)
    assert all(c.status != LegalChangeStatus.SCHEDULED for c in changes)
    assert cases[0].status == "needs_review" and runs[0].result["problems"]


async def test_missing_event_from_full_diff_becomes_visible_exception(session_factory):
    service, _ = await setup(session_factory)
    async with session_factory() as session:
        await session.delete(await session.get(LegalChange,2))
        await session.commit()
    await service.run()
    async with session_factory() as session:
        run = await session.get(AutomationRun,1)
        assert run.status == "needs_review" and any("Пропущены" in p for p in run.result["problems"])
        assert (await session.get(LegalChange,1)).status == LegalChangeStatus.DRAFT


async def test_lock_recovery_and_bounded_provider_retries(session_factory):
    reviewer = AsyncMock()
    reviewer.review.side_effect = VerificationUnavailable("HTTP 503")
    service, _ = await setup(session_factory, verifier=reviewer)
    async with service.repository.lock() as acquired:
        assert acquired and (await service.run())["already_running"]
    for _attempt in range(3):
        async with session_factory() as session:
            case = await session.get(AutomationCase,1)
            if case:
                case.next_attempt_at = datetime(2000,1,1,tzinfo=UTC)
                await session.commit()
        await service.run()
    changes, cases, runs = await records(session_factory)
    assert cases[0].status == "needs_review" and cases[0].attempts == len(runs) == 3
    assert all(c.status == LegalChangeStatus.DRAFT for c in changes)
    assert (await service.run())["processed"] == 0


async def test_cancelled_worker_leaves_trace_and_next_worker_recovers(session_factory):
    reviewer = AsyncMock()
    reviewer.review.side_effect = asyncio.CancelledError
    service, _ = await setup(session_factory, verifier=reviewer)
    with pytest.raises(asyncio.CancelledError):
        await service.run()
    reviewer.review.side_effect = None
    reviewer.review.return_value = (report(), {})
    await service.run()
    _, cases, runs = await records(session_factory)
    assert runs[0].status == "interrupted" and cases[0].status == "error"


async def test_auto_approval_waits_for_date_then_delivery_requires_matching_receipt(session_factory, monkeypatch):
    service, configuration = await setup(session_factory)
    await service.run()
    requests = []
    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        number = request.url.path.rsplit('/',1)[-1]
        return httpx.Response(200,json={"operation_id":"run-"+number,"status":"succeeded","warnings":[],"document_id":"test-code",
                                      "section_number":number,"version":payload["revision_date"],"integrity_verified":True,
                                      "input_sha256":hashlib.sha256(payload["raw_text"].encode()).hexdigest(),"chunks_count":1})
    async with session_factory() as session, httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        processor = ProcessingService(LegalChangesRepository(session), service.pravo,
                                      RagClient(client,RagSettings(_env_file=None,rag_service_api_key="test")),
                                      configuration_provider=configuration, journal=DeliveryJournal(session_factory),
                                      automation_repository=service.repository)
        assert (await processor.run_processing()).changes_selected == 0
        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2027,3,1,8,tzinfo=UTC)
        monkeypatch.setattr("app.services.processing.datetime", Future)
        result = await processor.run_processing()
        assert result.changes_sent == 2 and len(requests) == 2
    changes, cases, _ = await records(session_factory)
    assert all(c.status == LegalChangeStatus.SENT for c in changes)
    assert cases[0].status == 'delivered'
    async with session_factory() as session:
        attempts = list(await session.scalars(select(DeliveryAttempt)))
        assert len(attempts) == 2 and all(a.status == 'succeeded' for a in attempts)


@pytest.mark.parametrize("change_kind", ["source", "metadata"])
async def test_changed_text_or_metadata_cannot_reuse_auto_approval(session_factory, change_kind):
    service, _ = await setup(session_factory)
    await service.run()
    async with session_factory() as session:
        repository = LegalChangesRepository(session)
        change = await repository.get_by_id(1)
        if change_kind == "metadata":
            change.section_title = "Изменённый заголовок"
            await session.commit()
        rag = AsyncMock()
        processor = ProcessingService(repository, service.pravo, rag, delivery_enabled=True)
        if change_kind == "source":
            processor.section_text.get_text = AsyncMock(return_value="Подменённый текст статьи")
        from app.exceptions.rag import DeliveryVerificationError
        with pytest.raises(DeliveryVerificationError):
            await processor._process_change(change,{})
        rag.update_section.assert_not_called()


async def test_automation_admin_exposes_trace_and_retry_is_protected(admin_client, session_factory, monkeypatch):
    import app.admin.automation
    monkeypatch.setattr(app.admin.automation,"get_settings", app.admin.get_settings)
    service, _ = await setup(session_factory, mode="shadow")
    await service.run()
    assert (await admin_client.get('/admin/automation')).status_code == 302
    await admin_client.post('/admin/login',data={'username':'operator','password':'test-admin-password'})
    listing = await admin_client.get('/admin/automation')
    assert listing.status_code == 200 and 'Проверено без отправки' in listing.text
    response = await admin_client.get('/admin/automation/1')
    assert response.status_code == 200 and 'Обе поправки' in response.text and 'Контрольная сумма' in response.text
    assert (await admin_client.post('/admin/automation/1/retry')).status_code == 403
    token = BeautifulSoup(response.text,'html.parser').select_one('input[name=csrf_token]')['value']
    assert (await admin_client.post('/admin/automation/1/retry',data={'csrf_token':token})).status_code == 303


async def test_retry_after_partial_delivery_preserves_sent_event_and_resets_failed(session_factory):
    service, _ = await setup(session_factory)
    await service.run()
    async with session_factory() as session:
        sent = await session.get(LegalChange, 1)
        sent.status = LegalChangeStatus.SENT
        sent.sent_at = datetime.now(UTC)
        sent.rag_response = {'operation_id': 'already-delivered'}
        failed = await session.get(LegalChange, 2)
        failed.status, failed.retry_count, failed.last_error = LegalChangeStatus.FAILED, 3, 'HTTP 503'
        await session.commit()
    await service.repository.delivery_exception(failed, 'Лимит попыток')
    assert await service.repository.retry(1)
    await service.run()
    changes, cases, runs = await records(session_factory)
    assert cases[0].status == 'approved' and len(runs) == 2
    assert changes[0].status == LegalChangeStatus.SENT and changes[0].automation_run_id == runs[0].id
    assert changes[0].rag_response == {'operation_id': 'already-delivered'}
    assert changes[1].status == LegalChangeStatus.SCHEDULED and changes[1].retry_count == 0
    assert changes[1].automation_run_id == runs[1].id


async def test_later_source_discovery_keeps_one_case_without_replacing_known_law(session_factory):
    service, _ = await setup(session_factory)
    await service.repository.ensure_case(1, 2, None)
    await service.repository.ensure_case(1, 2, LAW_HASH)
    await service.repository.ensure_case(1, 2, 'c' * 64)
    _, cases, _ = await records(session_factory)
    assert len(cases) == 1 and cases[0].amending_doc_hash == LAW_HASH


async def test_manual_resolution_closes_exception_but_keeps_llm_history(session_factory):
    from app.repositories.tracked_documents import TrackedDocumentsRepository
    from app.schemas.legal_changes import LegalChangeReviewRequest
    from app.services.legal_changes import LegalChangesService
    service, _ = await setup(session_factory)
    service.verifier.review.return_value[0].issues = ['Неоднозначность, нужен оператор']
    await service.run()
    async with session_factory() as session:
        reviewer = LegalChangesService(LegalChangesRepository(session), TrackedDocumentsRepository(session), service.repository)
        await reviewer.approve_change(1, LegalChangeReviewRequest(reviewed_by='operator'))
        assert (await records(session_factory))[1][0].status == 'needs_review'
        await reviewer.reject_change(2, LegalChangeReviewRequest(reviewed_by='operator', review_notes='Отменено'))
    changes, cases, runs = await records(session_factory)
    assert cases[0].status == 'resolved' and runs[0].status == 'needs_review'
    assert all(c.review_origin == 'human' for c in changes)
