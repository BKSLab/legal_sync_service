import asyncio
from datetime import UTC, date, datetime

import pytest
from app.db.models import LegalChange, LegalChangeStatus, TrackedDocument
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.legal_changes import LegalChangeCreateRequest
from sqlalchemy import select, text
from tests.conftest import TK_RF_DOC_HASH, TK_RF_REDACTION_ID
from tests.unit.services.test_processing_service import _build_service


@pytest.fixture
async def document_id(session_factory):
    async with session_factory() as session:
        document = TrackedDocument(
            document_id="tk-197-2001", short_name="Трудовой кодекс", full_title="Трудовой кодекс",
            category="labor_code", audience="both", topics=[], source_title="Трудовой кодекс",
            publication_block="president", document_number="197-ФЗ", adoption_date=date(2001, 12, 30),
            ebpi_doc_hash=TK_RF_DOC_HASH, monitor_from=date(2026, 1, 1),
        )
        session.add(document)
        await session.commit()
        return document.id


def _batch(document_id):
    return [
        LegalChangeCreateRequest(
            tracked_document_id=document_id, section_number=number, section_title=f"Статья {number}",
            amending_law_ref="от 26.07.2026 № 246-ФЗ", ebpi_redaction_id=TK_RF_REDACTION_ID,
            redaction_date=date(2026, 3, 1), effective_date=date(2026, 3, 1),
        )
        for number in ("57", "58", "59")
    ]


async def test_mid_batch_failure_rolls_back_every_section_and_allows_retry(session_factory, document_id):
    batch = _batch(document_id)
    broken_batch = [batch[0], batch[1].model_copy(update={"tracked_document_id": -1}), batch[2]]
    async with session_factory() as session:
        repository = LegalChangesRepository(session)
        with pytest.raises(LegalChangeRepositoryError):
            await repository.save_redaction_changes(broken_batch)

        assert await repository.get_known_redaction_ids(document_id) == set()
        assert (await session.execute(select(LegalChange))).scalars().all() == []
        assert await repository.save_redaction_changes(batch) == 3
        assert await repository.save_redaction_changes(batch) == 0
        assert await repository.get_known_redaction_ids(document_id) == {TK_RF_REDACTION_ID}
        rows = (await session.execute(select(LegalChange))).scalars().all()
        assert {row.section_number for row in rows} == {"57", "58", "59"}


async def test_concurrent_monitoring_saves_complete_batch_once(session_factory, document_id):
    async def save_batch():
        async with session_factory() as session:
            return await LegalChangesRepository(session).save_redaction_changes(_batch(document_id))

    counts = await asyncio.wait_for(asyncio.gather(save_batch(), save_batch()), timeout=10)

    assert sorted(counts) == [0, 3]
    async with session_factory() as session:
        rows = (await session.execute(select(LegalChange))).scalars().all()
        assert len(rows) == 3
        assert {row.section_number for row in rows} == {"57", "58", "59"}


async def _schedule(session_factory, document_id):
    async with session_factory() as session:
        change = await LegalChangesRepository(session).save(_batch(document_id)[2])
        change.status = LegalChangeStatus.SCHEDULED
        await session.commit()
        return change.id


@pytest.mark.parametrize("failure", ["cancelled", "database"])
async def test_restart_recovers_delivery_interrupted_after_rag_received_it(
    session_factory, document_id, redaction_html, redaction_content_nodes, failure,
):
    change_id = await _schedule(session_factory, document_id)
    service, _, _, rag_client = _build_service(redaction_html, redaction_content_nodes, [])
    if failure == "cancelled":
        rag_client.update_section.side_effect = asyncio.CancelledError()

    async with session_factory() as session:
        if failure == "database":
            await session.execute(text(
                "ALTER TABLE legal_changes ADD CONSTRAINT test_reject_sent CHECK (status != 'sent')",
            ))
            await session.commit()
        service.legal_changes_repository = LegalChangesRepository(session)
        error_class = asyncio.CancelledError if failure == "cancelled" else LegalChangeRepositoryError
        with pytest.raises(error_class):
            await service.run_processing()

    async with session_factory() as session:
        interrupted = await session.get(LegalChange, change_id)
        assert interrupted.status == LegalChangeStatus.PROCESSING
        assert interrupted.retry_count == 0
        if failure == "database":
            await session.execute(text("ALTER TABLE legal_changes DROP CONSTRAINT test_reject_sent"))
            await session.commit()

    rag_client.update_section.side_effect = None
    async with session_factory() as session:
        service.legal_changes_repository = LegalChangesRepository(session)
        result = await service.run_processing()
        sent = await session.get(LegalChange, change_id)
        assert result.changes_recovered == result.changes_sent == 1
        assert sent.status == LegalChangeStatus.SENT
        assert sent.last_error is None
        assert rag_client.update_section.await_count == 2

        repeated = await service.run_processing()
        assert repeated.changes_selected == repeated.changes_recovered == 0


async def test_second_run_cannot_recover_or_send_event_of_active_worker(
    session_factory, document_id, redaction_html, redaction_content_nodes,
):
    await _schedule(session_factory, document_id)
    entered_rag = asyncio.Event()
    finish_rag = asyncio.Event()
    first, _, _, first_rag = _build_service(redaction_html, redaction_content_nodes, [])
    second, _, _, second_rag = _build_service(redaction_html, redaction_content_nodes, [])

    async def send(**kwargs):
        entered_rag.set()
        await finish_rag.wait()
        return {"chunks_count": 1}

    first_rag.update_section.side_effect = send
    async with session_factory() as first_session, session_factory() as second_session:
        first.legal_changes_repository = LegalChangesRepository(first_session)
        second.legal_changes_repository = LegalChangesRepository(second_session)
        task = asyncio.create_task(first.run_processing())
        try:
            await asyncio.wait_for(entered_rag.wait(), timeout=10)
            skipped = await second.run_processing()
            assert skipped.already_running is True
            assert skipped.changes_recovered == skipped.changes_selected == 0
            second_rag.update_section.assert_not_awaited()
        finally:
            finish_rag.set()
            await asyncio.wait_for(task, timeout=10)

        assert (await second.run_processing()).changes_selected == 0


async def test_recovery_does_not_reopen_terminal_events_or_bypass_retry_limit(session_factory, document_id):
    async with session_factory() as session:
        repository = LegalChangesRepository(session)
        batch = _batch(document_id)
        rows = [await repository.save(data) for data in batch]
        rows[0].status = LegalChangeStatus.SENT
        rows[1].status = LegalChangeStatus.CANCELLED
        rows[2].status = LegalChangeStatus.FAILED
        rows[2].retry_count = 3
        await session.commit()

        async with repository.processing_lock() as acquired:
            assert acquired
            assert await repository.recover_interrupted_processing() == 0
            assert await repository.get_due_for_sending(datetime.now(UTC), max_retries=3) == []

        await session.refresh(rows[0])
        await session.refresh(rows[1])
        assert rows[0].status == LegalChangeStatus.SENT
        assert rows[1].status == LegalChangeStatus.CANCELLED
