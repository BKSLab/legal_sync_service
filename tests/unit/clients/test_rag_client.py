from datetime import date

import httpx
import pytest
from app.clients.rag import RagClient
from app.core.settings import RagSettings
from app.exceptions.rag import RagClientError, RagRejectedError, RagStaleRevisionError
from pydantic import SecretStr


def _build_client(handler) -> RagClient:
    settings = RagSettings(
        rag_service_base_url="http://rag.test",
        rag_service_api_key=SecretStr("test-key"),
    )
    return RagClient(
        httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=settings,
    )


async def _send(client: RagClient, **overrides):
    payload = {
        "document_id": "labor_code_rf",
        "section_number": "59",
        "category": "labor_code",
        "raw_text": "Статья 59. Срочный трудовой договор ...",
        "section_title": "Срочный трудовой договор",
        "revision_date": date(2027, 3, 1),
        "audience": "both",
        "source_title": '"Трудовой кодекс Российской Федерации" от 30.12.2001 № 197-ФЗ',
        "topics": [],
        "amending_act_type": "Федеральный закон",
        "amending_act_number": "246-ФЗ",
        "amending_act_date": date(2026, 7, 26),
    }
    payload.update(overrides)
    return await client.update_section(**payload)


@pytest.mark.asyncio
async def test_payload_matches_rag_contract():
    """RAG ждёт `topics` списком, дату редакции и реквизиты изменившего акта."""

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["api_key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"document_id": "labor_code_rf", "chunks_count": 3})

    client = _build_client(handler)
    await _send(client)

    assert captured["url"] == "http://rag.test/api/v1/document/labor_code_rf/sections/59"
    assert captured["api_key"] == "test-key"
    assert captured["body"]["topics"] == []
    assert captured["body"]["revision_date"] == "2027-03-01"
    assert captured["body"]["section_title"] == "Срочный трудовой договор"
    assert captured["body"]["amending_act_number"] == "246-ФЗ"
    assert captured["body"]["amending_act_date"] == "2026-07-26"
    assert "topic" not in captured["body"]
    assert "version" not in captured["body"]


@pytest.mark.asyncio
async def test_validation_error_is_not_retryable():
    client = _build_client(
        lambda request: httpx.Response(422, text="category не поддерживает обновление")
    )

    with pytest.raises(RagRejectedError):
        await _send(client)


@pytest.mark.asyncio
async def test_server_error_is_retryable_class():
    client = _build_client(lambda request: httpx.Response(500, text="oops"))

    with pytest.raises(RagClientError) as error:
        await _send(client)
    assert not isinstance(error.value, RagRejectedError)


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_request():
    settings = RagSettings(rag_service_base_url="http://rag.test", rag_service_api_key=None)
    client = RagClient(httpx_client=httpx.AsyncClient(), settings=settings)

    with pytest.raises(RagClientError):
        await _send(client)


async def test_stale_revision_conflict_is_distinguished_from_other_rejections():
    response = {"detail": {
        "code": "stale_revision",
        "message": "Уже загружена более новая редакция",
        "revision_date": "2027-03-01",
        "current_revision_date": "2027-04-01",
    }}
    client = _build_client(lambda request: httpx.Response(409, json=response))

    with pytest.raises(RagStaleRevisionError) as error:
        await _send(client)

    assert error.value.response == response


@pytest.mark.parametrize("body", [{"detail": "Другой конфликт"}, [], {"detail": []}])
async def test_other_conflicts_do_not_cancel_events_as_stale(body):
    client = _build_client(lambda request: httpx.Response(409, json=body))

    with pytest.raises(RagRejectedError) as error:
        await _send(client)

    assert not isinstance(error.value, RagStaleRevisionError)
