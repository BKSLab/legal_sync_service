from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.db.models.legal_changes import LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeInvalidStatusError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.legal_changes import LegalChangeReviewRequest
from app.services.legal_changes import LegalChangesService


def _change(**overrides):
    values = {
        "id": 1,
        "tracked_document_id": 1,
        "section_number": "128",
        "section_title": "Отпуск без сохранения заработной платы",
        "amending_law_ref": "0001202601010001",
        "adoption_date": date(2026, 1, 1),
        "effective_date": date(2026, 2, 1),
        "change_description": "Статья изменена.",
        "delta_text": "статью 128 изложить в следующей редакции",
        "consolidated_text": None,
        "consolidated_text_source": None,
        "audience_override": None,
        "topic_override": None,
        "source_title_override": None,
        "status": LegalChangeStatus.DRAFT,
        "send_at": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "sent_at": None,
        "rag_response": None,
        "retry_count": 0,
        "last_error": None,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_approve_change_moves_draft_to_scheduled():
    changes_repository = AsyncMock(spec=LegalChangesRepository)
    documents_repository = AsyncMock(spec=TrackedDocumentsRepository)
    draft_change = _change()
    scheduled_change = _change(
        status=LegalChangeStatus.SCHEDULED,
        reviewed_by="admin",
        reviewed_at=datetime.now(tz=UTC),
    )
    changes_repository.get_by_id.return_value = draft_change
    changes_repository.update.return_value = scheduled_change
    service = LegalChangesService(
        legal_changes_repository=changes_repository,
        tracked_documents_repository=documents_repository,
    )
    data = LegalChangeReviewRequest(reviewed_by="admin")

    result = await service.approve_change(change_id=1, data=data)

    assert result.status == LegalChangeStatus.SCHEDULED
    assert result.reviewed_by == "admin"
    changes_repository.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_change_raises_when_status_is_not_draft():
    changes_repository = AsyncMock(spec=LegalChangesRepository)
    documents_repository = AsyncMock(spec=TrackedDocumentsRepository)
    changes_repository.get_by_id.return_value = _change(status=LegalChangeStatus.SENT)
    service = LegalChangesService(
        legal_changes_repository=changes_repository,
        tracked_documents_repository=documents_repository,
    )
    data = LegalChangeReviewRequest(reviewed_by="admin")

    with pytest.raises(LegalChangeInvalidStatusError):
        await service.approve_change(change_id=1, data=data)
