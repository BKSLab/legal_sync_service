import json
from datetime import date

import httpx
import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.core.settings import PravoEbpiSettings
from app.exceptions.pravo_ebpi import (
    PravoEbpiDocumentNotFoundError,
    PravoEbpiRequestError,
    PravoEbpiResponseError,
)
from tests.conftest import TK_RF_DOC_HASH


def _build_client(handler) -> PravoEbpiClient:
    settings = PravoEbpiSettings(
        pravo_ebpi_base_url="http://portal.test/api/ebpi",
        pravo_ebpi_max_retries=2,
        pravo_ebpi_retry_delay_seconds=0,
    )
    transport = httpx.MockTransport(handler)
    return PravoEbpiClient(
        httpx_client=httpx.AsyncClient(transport=transport),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_search_documents_parses_portal_dates(search_payload):
    client = _build_client(lambda request: httpx.Response(200, json=search_payload))

    results = await client.search_documents(document_number="197-ФЗ", title_words="Трудовой кодекс")

    assert len(results) == 1
    assert results[0].doc_hash == TK_RF_DOC_HASH
    assert results[0].adoption_date == date(2001, 12, 30)


@pytest.mark.asyncio
async def test_search_query_carries_mandatory_sort_attribute(search_payload):
    """Без атрибута сортировки портал возвращает счётчик, но пустой список."""

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = json.loads(request.url.params["q"])
        captured["bank"] = request.url.params["bpa"]
        return httpx.Response(200, json=search_payload)

    client = _build_client(handler)
    await client.search_documents(document_number="197-ФЗ", title_words="Трудовой кодекс")

    attribute_ids = [attribute["AttrId"] for attribute in captured["query"]]
    assert PravoEbpiClient.ATTR_SORT in attribute_ids
    assert captured["bank"] == "ebpi"
    sort_attribute = next(a for a in captured["query"] if a["AttrId"] == PravoEbpiClient.ATTR_SORT)
    assert sort_attribute["AttrMode"] == 1


@pytest.mark.asyncio
async def test_get_document_card_returns_hash(card_payload):
    client = _build_client(lambda request: httpx.Response(200, json=card_payload))

    card = await client.get_document_card(publication_number="0001202607260006")

    assert card.doc_hash == "797105206ac17902a9990082c79054b4f646947a5415d712d44ff90bf4ce8dd0"


@pytest.mark.asyncio
async def test_get_document_card_raises_when_document_absent():
    client = _build_client(lambda request: httpx.Response(200, json={"docid": 0, "error": ""}))

    with pytest.raises(PravoEbpiDocumentNotFoundError):
        await client.get_document_card(publication_number="0001000000000000")


@pytest.mark.asyncio
async def test_get_redactions_parses_effective_dates(redactions_payload):
    client = _build_client(lambda request: httpx.Response(200, json=redactions_payload))

    redactions = await client.get_redactions(document_hash=TK_RF_DOC_HASH)

    assert redactions[0].redaction_id == 495396
    assert redactions[0].redaction_date == date(2027, 3, 1)
    assert redactions[0].is_completed is True


@pytest.mark.asyncio
async def test_get_redactions_raises_when_portal_reports_error():
    client = _build_client(
        lambda request: httpx.Response(200, json={"redactions": [], "error": "Документ не найден"})
    )

    with pytest.raises(PravoEbpiDocumentNotFoundError):
        await client.get_redactions(document_hash="0" * 64)


@pytest.mark.asyncio
async def test_get_redaction_text_rejects_empty_body():
    client = _build_client(lambda request: httpx.Response(200, json={"redtext": "", "error": None}))

    with pytest.raises(PravoEbpiResponseError):
        await client.get_redaction_text(redaction_id=1)


@pytest.mark.asyncio
async def test_unexpected_structure_raises_instead_of_silent_partial_parse():
    client = _build_client(lambda request: httpx.Response(200, json={"docs": "не список"}))

    with pytest.raises(PravoEbpiResponseError):
        await client.search_documents(document_number="197-ФЗ", title_words="Трудовой кодекс")


@pytest.mark.asyncio
async def test_server_error_is_retried_then_fails():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503)

    client = _build_client(handler)

    with pytest.raises(PravoEbpiRequestError):
        await client.get_redaction_text(redaction_id=1)
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_client_error_is_not_retried():
    """Повтор на 4xx — трата бюджета: ответ не изменится."""

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(400)

    client = _build_client(handler)

    with pytest.raises(PravoEbpiRequestError):
        await client.get_redaction_text(redaction_id=1)
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_timeout_is_retried_and_then_succeeds(redactions_payload):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200, json=redactions_payload)

    client = _build_client(handler)

    redactions = await client.get_redactions(document_hash=TK_RF_DOC_HASH)

    assert attempts["count"] == 2
    assert redactions
