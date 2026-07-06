from datetime import UTC, datetime
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
        "category": "law",
        "audience": "hr",
        "topic": "labor",
        "source_title": "Трудовой кодекс РФ",
        "publication_block": "president",
        "consolidated_source_url": "https://example.org/labor-code",
        "is_active": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_create_document_returns_created_schema():
    repository = AsyncMock(spec=TrackedDocumentsRepository)
    repository.save.return_value = _document()
    service = TrackedDocumentsService(repository=repository)
    data = TrackedDocumentCreateRequest(
        document_id="labor_code_rf",
        short_name="Трудовой кодекс",
        full_title="Трудовой кодекс Российской Федерации",
        category="law",
        audience="hr",
        topic="labor",
        source_title="Трудовой кодекс РФ",
        publication_block="president",
        consolidated_source_url="https://example.org/labor-code",
    )

    result = await service.create_document(data=data)

    assert result.id == 1
    assert result.document_id == "labor_code_rf"
    repository.save.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_get_document_raises_not_found_when_repository_returns_none():
    repository = AsyncMock(spec=TrackedDocumentsRepository)
    repository.get_by_id.return_value = None
    service = TrackedDocumentsService(repository=repository)

    with pytest.raises(TrackedDocumentNotFoundError):
        await service.get_document(document_id=404)
