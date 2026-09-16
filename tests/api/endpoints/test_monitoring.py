from unittest.mock import AsyncMock

import pytest
from app.api.v1.endpoints.monitoring import router
from app.dependencies import services
from app.dependencies.auth import verify_api_key
from app.dependencies.clients import get_pravo_ebpi_client, get_rag_client
from app.dependencies.db_session import get_db_session
from app.exceptions.configuration import ConfigurationUnavailableError
from app.schemas.configuration import ConfigurationValues
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.parametrize("unavailable", [False, True])
async def test_manual_processing_respects_disabled_or_unavailable_configuration(monkeypatch, unavailable):
    db_session = AsyncMock()
    rag_client = AsyncMock()
    ebpi_client = AsyncMock()

    async def session():
        yield db_session

    provider = AsyncMock(return_value=ConfigurationValues())
    if unavailable:
        provider.side_effect = ConfigurationUnavailableError()
    monkeypatch.setattr(services, "load_configuration", provider)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_rag_client] = lambda: rag_client
    app.dependency_overrides[get_pravo_ebpi_client] = lambda: ebpi_client
    app.dependency_overrides[verify_api_key] = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/monitoring/process")

    if unavailable:
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        assert response.json()["delivery_disabled"] is True
        assert response.json()["changes_sent"] == 0
    assert db_session.mock_calls == []
    assert rag_client.mock_calls == []
    assert ebpi_client.mock_calls == []
