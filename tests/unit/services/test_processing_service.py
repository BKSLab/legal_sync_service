from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.clients.rag import RagClient
from app.db.models.legal_changes import LegalChangeStatus
from app.exceptions.pravo_ebpi import PravoEbpiRequestError
from app.exceptions.rag import RagClientError, RagRejectedError, RagStaleRevisionError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.pravo_ebpi import EbpiRedaction
from app.services.processing import ProcessingService
from tests.conftest import TK_RF_DOC_HASH, TK_RF_REDACTION_ID


def _tracked_document(**overrides):
    values = {
        "document_id": "labor_code_rf",
        "short_name": "Трудовой кодекс",
        "category": "labor_code",
        "audience": "both",
        "topics": [],
        "source_title": "Трудовой кодекс Российской Федерации от 30.12.2001 № 197-ФЗ",
        "ebpi_doc_hash": TK_RF_DOC_HASH,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _change(**overrides):
    values = {
        "id": 10,
        "section_number": "59",
        "section_title": "Срочный трудовой договор",
        "ebpi_redaction_id": TK_RF_REDACTION_ID,
        "redaction_date": date(2027, 3, 1),
        "effective_date": date(2027, 3, 1),
        "audience_override": None,
        "source_title_override": None,
        "topics_override": None,
        "amending_act_type": "Федеральный закон",
        "amending_act_number": "246-ФЗ",
        "amending_act_date": date(2026, 7, 26),
        "retry_count": 0,
        "status": LegalChangeStatus.SCHEDULED,
        "tracked_document": _tracked_document(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _redaction(**overrides) -> EbpiRedaction:
    values = {
        "redid": TK_RF_REDACTION_ID,
        "reddate": "20270301",
        "redcompleted": True,
        "hascontent": True,
        "redinitial": False,
        "actual": False,
    }
    values.update(overrides)
    return EbpiRedaction.model_validate(values)


def _build_service(redaction_html, redaction_content_nodes, changes):
    repository = AsyncMock(spec=LegalChangesRepository)
    repository.processing_lock.return_value.__aenter__.return_value = True
    repository.recover_interrupted_processing.return_value = 0
    repository.get_due_for_sending.return_value = changes
    repository.update.side_effect = lambda change, values: change

    ebpi_client = AsyncMock(spec=PravoEbpiClient)
    ebpi_client.get_redactions.return_value = [_redaction()]
    ebpi_client.get_redaction_text.return_value = redaction_html
    ebpi_client.get_redaction_content.return_value = redaction_content_nodes

    rag_client = AsyncMock(spec=RagClient)
    rag_client.update_section.return_value = {"document_id": "labor_code_rf", "chunks_count": 4}

    service = ProcessingService(
        legal_changes_repository=repository,
        pravo_ebpi_client=ebpi_client,
        rag_client=rag_client,
        max_retries=3,
    )
    return service, repository, ebpi_client, rag_client


@pytest.mark.asyncio
async def test_sends_consolidated_section_text_to_rag(redaction_html, redaction_content_nodes):
    service, _, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [_change()]
    )

    result = await service.run_processing()

    assert result.changes_sent == 1
    payload = rag_client.update_section.await_args.kwargs
    assert payload["document_id"] == "labor_code_rf"
    assert payload["section_number"] == "59"
    assert payload["raw_text"].startswith("Статья 59. Срочный трудовой договор")
    assert payload["revision_date"] == date(2027, 3, 1)
    assert payload["topics"] == []
    assert payload["amending_act_number"] == "246-ФЗ"


@pytest.mark.asyncio
async def test_redaction_is_parsed_once_for_all_its_sections(
    redaction_html, redaction_content_nodes
):
    """Редакция кодекса весит мегабайты — качать её на каждую статью нельзя."""

    changes = [
        _change(id=1, section_number="57", section_title="Содержание трудового договора"),
        _change(id=2, section_number="58", section_title="Срок трудового договора"),
        _change(id=3, section_number="59", section_title="Срочный трудовой договор"),
    ]
    service, _, ebpi_client, rag_client = _build_service(
        redaction_html, redaction_content_nodes, changes
    )

    result = await service.run_processing()

    assert result.changes_sent == 3
    assert ebpi_client.get_redaction_text.await_count == 1
    assert rag_client.update_section.await_count == 3


@pytest.mark.asyncio
async def test_incomplete_redaction_postpones_and_returns_to_scheduled(
    redaction_html, redaction_content_nodes
):
    """Незавершённый текст отдаётся порталом частично — отправлять его нельзя."""

    change = _change()
    service, repository, ebpi_client, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [change]
    )
    ebpi_client.get_redactions.return_value = [_redaction(redcompleted=False)]

    result = await service.run_processing()

    assert result.changes_postponed == 1
    assert result.changes_sent == 0
    rag_client.update_section.assert_not_awaited()
    # Событие обязано вернуться в scheduled, иначе выпадет из выборки навсегда.
    statuses = [call.kwargs["values"].get("status") for call in repository.update.await_args_list]
    assert statuses[-1] == LegalChangeStatus.SCHEDULED


@pytest.mark.asyncio
async def test_rag_rejection_marks_change_failed(redaction_html, redaction_content_nodes):
    service, repository, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [_change()]
    )
    rag_client.update_section.side_effect = RagRejectedError("category не поддерживается")

    result = await service.run_processing()

    assert result.changes_failed == 1
    values = repository.update.await_args_list[-1].kwargs["values"]
    assert values["status"] == LegalChangeStatus.FAILED
    assert values["retry_count"] == 1
    assert "category" in values["last_error"]


@pytest.mark.asyncio
async def test_portal_failure_marks_change_failed(redaction_html, redaction_content_nodes):
    service, repository, ebpi_client, _ = _build_service(
        redaction_html, redaction_content_nodes, [_change()]
    )
    ebpi_client.get_redaction_text.side_effect = PravoEbpiRequestError("портал недоступен")

    result = await service.run_processing()

    assert result.changes_failed == 1
    assert repository.update.await_args_list[-1].kwargs["values"]["status"] == LegalChangeStatus.FAILED


@pytest.mark.asyncio
async def test_one_failed_change_does_not_block_the_rest(
    redaction_html, redaction_content_nodes
):
    changes = [_change(id=1, section_number="999"), _change(id=2, section_number="59")]
    service, _, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, changes
    )

    result = await service.run_processing()

    assert result.changes_failed == 1
    assert result.changes_sent == 1
    assert rag_client.update_section.await_count == 1


@pytest.mark.asyncio
async def test_overrides_take_precedence_over_document_defaults(
    redaction_html, redaction_content_nodes
):
    change = _change(
        audience_override="employer",
        source_title_override="Иное наименование",
        topics_override=["охрана труда"],
    )
    service, _, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [change]
    )

    await service.run_processing()

    payload = rag_client.update_section.await_args.kwargs
    assert payload["audience"] == "employer"
    assert payload["source_title"] == "Иное наименование"
    assert payload["topics"] == ["охрана труда"]


@pytest.mark.asyncio
async def test_successful_send_records_source_and_response(
    redaction_html, redaction_content_nodes
):
    service, repository, _, _ = _build_service(
        redaction_html, redaction_content_nodes, [_change()]
    )

    await service.run_processing()

    values = repository.update.await_args_list[-1].kwargs["values"]
    assert values["status"] == LegalChangeStatus.SENT
    assert values["rag_response"] == {"document_id": "labor_code_rf", "chunks_count": 4}
    assert str(TK_RF_REDACTION_ID) in values["consolidated_text_source"]
    assert values["consolidated_text"].startswith("Статья 59.")


@pytest.mark.asyncio
async def test_transport_failure_is_retryable_and_counted(
    redaction_html, redaction_content_nodes
):
    service, repository, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [_change(retry_count=1)]
    )
    rag_client.update_section.side_effect = RagClientError("RAG недоступен")

    result = await service.run_processing()

    assert result.changes_failed == 1
    assert repository.update.await_args_list[-1].kwargs["values"]["retry_count"] == 2


async def test_unexpected_failure_is_retryable_and_does_not_block_other_events(
    redaction_html, redaction_content_nodes,
):
    service, repository, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [_change(id=1), _change(id=2)],
    )
    rag_client.update_section.side_effect = [RuntimeError("Неожиданный сбой"), {}]

    result = await service.run_processing()

    assert result.changes_failed == result.changes_sent == 1
    failed_values = repository.update.await_args_list[1].kwargs["values"]
    assert failed_values["status"] == LegalChangeStatus.FAILED
    assert failed_values["retry_count"] == 1
    assert "Неожиданный сбой" in failed_values["last_error"]


async def test_stale_revision_is_cancelled_without_retry(redaction_html, redaction_content_nodes):
    service, repository, _, rag_client = _build_service(
        redaction_html, redaction_content_nodes, [_change()],
    )
    response = {"detail": {"code": "stale_revision", "message": "Уже есть более новая редакция"}}
    rag_client.update_section.side_effect = RagStaleRevisionError(response)

    result = await service.run_processing()

    assert result.changes_superseded == 1
    assert result.changes_failed == result.changes_sent == 0
    values = repository.update.await_args_list[-1].kwargs["values"]
    assert values["status"] == LegalChangeStatus.CANCELLED
    assert values["rag_response"] == response
    assert "retry_count" not in values
