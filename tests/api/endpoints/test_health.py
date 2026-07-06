import pytest
from app.api.v1.endpoints.health import router
from app.dependencies.db_session import get_db_session
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class FakeSession:
    async def execute(self, *_args, **_kwargs):
        return None


@pytest.fixture
def app_with_fake_db():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db_session():
        yield FakeSession()

    app.dependency_overrides[get_db_session] = override_db_session
    return app


@pytest.mark.asyncio
async def test_health_returns_ok(app_with_fake_db):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_db),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
