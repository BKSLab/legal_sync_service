from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from app.db.models.legal_changes import LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeNotFoundError, LegalChangePreviewConflictError
from app.exceptions.pravo_ebpi import PravoEbpiRequestError
from app.exceptions.redaction import RedactionParseError, RedactionSectionNotFoundError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.processing_preview import ProcessingPreviewRequest
from app.services.processing_preview import ProcessingPreviewService
from tests.unit.services.test_processing_service import _build_service, _change, _redaction


@pytest.fixture
def preview_setup(redaction_html, redaction_content_nodes):
    change = _change(
        status=LegalChangeStatus.DRAFT, send_at=datetime(2027, 3, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 16, tzinfo=UTC),
    )
    processing, _, portal, rag = _build_service(redaction_html, redaction_content_nodes, [change])
    repository = AsyncMock(spec=LegalChangesRepository)
    repository.processing_lock.return_value.__aenter__.return_value = True
    repository.get_by_id.return_value = change
    repository.save_preview_text.return_value = True
    service = ProcessingPreviewService(repository, processing.section_text)
    return service, repository, portal, rag, change


@pytest.mark.parametrize("as_of", ["2027-02-28T23:59:59Z", "2027-03-01T02:59:59+03:00"])
async def test_before_due_time_does_not_fetch_or_save(preview_setup, as_of):
    service, repository, portal, rag, change = preview_setup

    result = await service.preview_change(change.id, ProcessingPreviewRequest(as_of=as_of))

    assert result.outcome == "not_due"
    assert result.consolidated_text is None
    repository.save_preview_text.assert_not_awaited()
    assert portal.mock_calls == rag.mock_calls == []


@pytest.mark.parametrize("as_of", ["2027-03-01T00:00:00Z", "2027-03-01T03:00:00+03:00"])
async def test_due_draft_extracts_exact_article_and_only_saves_text(preview_setup, as_of):
    service, repository, portal, rag, change = preview_setup

    result = await service.preview_change(change.id, ProcessingPreviewRequest(as_of=as_of))

    assert result.outcome == "extracted"
    assert result.event_status == change.status == LegalChangeStatus.DRAFT
    assert change.retry_count == 0
    assert result.rag_sent is False
    assert result.consolidated_text.startswith("Статья 59. Срочный трудовой договор")
    assert "Статья 60." not in result.consolidated_text
    assert result.text_length == len(result.consolidated_text)
    assert result.consolidated_text_source.endswith("495396 от 2027-03-01")
    repository.save_preview_text.assert_awaited_once_with(
        change, result.consolidated_text, result.consolidated_text_source,
    )
    repository.update.assert_not_awaited()
    repository.get_due_for_sending.assert_not_awaited()
    repository.recover_interrupted_processing.assert_not_awaited()
    portal.get_redaction_text.assert_awaited_once_with(redaction_id=change.ebpi_redaction_id)
    assert rag.mock_calls == []


async def test_incomplete_redaction_is_not_saved(preview_setup):
    service, repository, portal, rag, change = preview_setup
    portal.get_redactions.return_value = [_redaction(redcompleted=False)]

    result = await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))

    assert result.outcome == "redaction_not_ready"
    portal.get_redaction_text.assert_not_awaited()
    repository.save_preview_text.assert_not_awaited()
    repository.update.assert_not_awaited()
    assert rag.mock_calls == []


@pytest.mark.parametrize("failure", ["portal", "wrong_redaction", "missing_section", "missing_hash"])
async def test_failed_extraction_preserves_event(preview_setup, failure):
    service, repository, portal, rag, change = preview_setup
    expected_error = RedactionParseError
    if failure == "portal":
        portal.get_redaction_text.side_effect = PravoEbpiRequestError("unavailable")
        expected_error = PravoEbpiRequestError
    elif failure == "wrong_redaction":
        portal.get_redactions.return_value = [_redaction(redid=1)]
    elif failure == "missing_section":
        change.section_number = "99999"
        expected_error = RedactionSectionNotFoundError
    else:
        change.tracked_document.ebpi_doc_hash = None

    with pytest.raises(expected_error):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))

    repository.save_preview_text.assert_not_awaited()
    repository.update.assert_not_awaited()
    assert change.status == LegalChangeStatus.DRAFT
    assert rag.mock_calls == []


@pytest.mark.parametrize("status", [LegalChangeStatus.PROCESSING, LegalChangeStatus.SENT, LegalChangeStatus.CANCELLED])
async def test_terminal_and_processing_events_are_not_overwritten(preview_setup, status):
    service, repository, portal, _, change = preview_setup
    change.status = status

    with pytest.raises(LegalChangePreviewConflictError):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))

    repository.save_preview_text.assert_not_awaited()
    assert portal.mock_calls == []


async def test_missing_due_date_requires_correction(preview_setup):
    service, repository, portal, _, change = preview_setup
    change.send_at = None
    with pytest.raises(LegalChangePreviewConflictError, match="срок"):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of="2027-03-01T00:00:00Z"))
    repository.save_preview_text.assert_not_awaited()
    assert portal.mock_calls == []


async def test_missing_event_returns_not_found(preview_setup):
    service, repository, portal, _, change = preview_setup
    repository.get_by_id.return_value = None
    with pytest.raises(LegalChangeNotFoundError):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))
    assert portal.mock_calls == []


async def test_busy_processing_lock_prevents_preview(preview_setup):
    service, repository, portal, _, change = preview_setup
    repository.processing_lock.return_value.__aenter__.return_value = False
    with pytest.raises(LegalChangePreviewConflictError, match="занята"):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))
    repository.get_by_id.assert_not_awaited()
    assert portal.mock_calls == []


async def test_concurrent_event_edit_is_reported_as_conflict(preview_setup):
    service, repository, _, _, change = preview_setup
    repository.save_preview_text.return_value = False
    with pytest.raises(LegalChangePreviewConflictError, match="изменилось"):
        await service.preview_change(change.id, ProcessingPreviewRequest(as_of=change.send_at))
