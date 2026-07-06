import httpx
import pytest
from app.clients.publication_pravo import PublicationPravoClient
from app.core.settings import PublicationPravoSettings


@pytest.mark.asyncio
async def test_get_daily_documents_returns_items_from_wrapped_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["PeriodType"] == "daily"
        return httpx.Response(200, json={"items": [{"eoNumber": "1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as httpx_client:
        client = PublicationPravoClient(
            httpx_client=httpx_client,
            settings=PublicationPravoSettings(),
        )

        result = await client.get_daily_documents(publication_block="president")

    assert result == [{"eoNumber": "1"}]
