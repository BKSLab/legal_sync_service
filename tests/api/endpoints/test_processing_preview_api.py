from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.v1.endpoints.legal_changes import router
from app.dependencies.services import get_processing_preview_service
from app.exceptions.legal_changes import LegalChangeNotFoundError, LegalChangePreviewConflictError
from app.exceptions.pravo_ebpi import PravoEbpiRequestError
from app.exceptions.redaction import RedactionSectionNotFoundError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr


@pytest.fixture
async def preview_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    service = AsyncMock()
    app.dependency_overrides[get_processing_preview_service] = lambda: service
    monkeypatch.setattr(
        "app.dependencies.auth.get_settings",
        lambda: SimpleNamespace(api=SimpleNamespace(api_key=SecretStr("test-key"))),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, service


async def test_preview_requires_api_key(preview_api):
    client, service = preview_api
    for headers in ({}, {"X-API-Key": "wrong-key"}):
        response = await client.post(
            "/api/v1/legal-changes/10/preview", headers=headers,
            json={"as_of": "2027-09-01T00:00:00Z"},
        )
        assert response.status_code == 401
    service.preview_change.assert_not_awaited()


@pytest.mark.parametrize("as_of", ["2027-09-01", "2027-09-01T00:00:00", "wrong-date"])
async def test_preview_rejects_ambiguous_clock_input(preview_api, as_of):
    client, service = preview_api
    response = await client.post(
        "/api/v1/legal-changes/10/preview", headers={"X-API-Key": "test-key"}, json={"as_of": as_of},
    )
    assert response.status_code == 422
    service.preview_change.assert_not_awaited()


@pytest.mark.parametrize("error,code", [
    (LegalChangeNotFoundError(10), 404),
    (LegalChangePreviewConflictError("Очередь занята"), 409),
    (PravoEbpiRequestError("unavailable"), 502),
    (RedactionSectionNotFoundError("14"), 404),
])
async def test_preview_errors_are_actionable(preview_api, error, code):
    client, service = preview_api
    service.preview_change.side_effect = error
    response = await client.post(
        "/api/v1/legal-changes/10/preview", headers={"X-API-Key": "test-key"},
        json={"as_of": "2027-09-01T00:00:00Z"},
    )
    assert response.status_code == code
    assert response.json()["detail"] == error.detail
