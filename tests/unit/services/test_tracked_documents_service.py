from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.exceptions.tracked_documents import TrackedDocumentNotFoundError
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.tracked_documents import TrackedDocumentCreateRequest
from app.services.tracked_documents import TrackedDocumentsService


def _document(**overrides):
    values = {
        "id": 1,
        "document_id": "labor_code_rf",
        "short_name": "Трудовой кодекс",
        "full_title": "Трудовой кодекс Российской Федерации",
        "category": "labor_code",
        "audience": "both",
        "topics": [],
        "source_title": "Трудовой кодекс Российской Федерации от 30.12.2001 № 197-ФЗ",
        "publication_block": "president",
        "document_number": "197-ФЗ",
        "adoption_date": date(2001, 12, 30),
        "monitor_from": date(2026, 9, 7),
        "ebpi_doc_hash": None,
        "ebpi_doc_id": None,
        "is_active": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _create_request(**overrides) -> TrackedDocumentCreateRequest:
    values = {
        "document_id": "labor_code_rf",
        "short_name": "Трудовой кодекс",
        "full_title": "Трудовой кодекс Российской Федерации",
        "category": "labor_code",
        "audience": "both",
        "topics": [],
        "source_title": "Трудовой кодекс Российской Федерации от 30.12.2001 № 197-ФЗ",
        "publication_block": "president",
        "document_number": "197-ФЗ",
        "adoption_date": date(2001, 12, 30),
    }
    values.update(overrides)
    return TrackedDocumentCreateRequest(**values)


@pytest.mark.asyncio
async def test_create_document_returns_created_schema():
    repository = AsyncMock(spec=TrackedDocumentsRepository)
    repository.save.return_value = _document()
    service = TrackedDocumentsService(repository=repository)
    data = _create_request(monitor_from=date(2026, 9, 7))

    result = await service.create_document(data=data)

    assert result.id == 1
    assert result.document_id == "labor_code_rf"
    repository.save.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_create_document_defaults_monitor_from_to_today():
    """Без даты начала наблюдения документ не должен порождать события по всей истории."""

    repository = AsyncMock(spec=TrackedDocumentsRepository)
    repository.save.return_value = _document()
    service = TrackedDocumentsService(repository=repository)

    await service.create_document(data=_create_request())

    saved = repository.save.await_args.args[0]
    assert saved.monitor_from == date.today()


@pytest.mark.asyncio
async def test_get_document_raises_not_found_when_repository_returns_none():
    repository = AsyncMock(spec=TrackedDocumentsRepository)
    repository.get_by_id.return_value = None
    service = TrackedDocumentsService(repository=repository)

    with pytest.raises(TrackedDocumentNotFoundError):
        await service.get_document(document_id=404)
