import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.core.settings import PravoEbpiSettings
from app.exceptions.pravo_ebpi import PravoEbpiRequestError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.pravo_ebpi import EbpiDocumentCard, EbpiRedaction, EbpiSearchResult
from app.services.monitoring import MonitoringService
from tests.conftest import FZ_246_DOC_HASH, TK_RF_DOC_HASH, TK_RF_REDACTION_ID


def _tracked_document(**overrides):
    values = {
        "id": 1,
        "document_id": "labor_code_rf",
        "short_name": "Трудовой кодекс",
        "full_title": "Трудовой кодекс Российской Федерации",
        "category": "labor_code",
        "document_number": "197-ФЗ",
        "adoption_date": date(2001, 12, 30),
        "monitor_from": date(2026, 9, 1),
        "ebpi_doc_hash": TK_RF_DOC_HASH,
        "ebpi_doc_id": 64284,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _redaction(**overrides) -> EbpiRedaction:
    values = {
        "redid": TK_RF_REDACTION_ID,
        "reddate": "20270301",
        "redcaption": "199. на 01.03.2027 (№ 246-ФЗ от 26.07.2026), не вступившая в силу редакция",
        "statename": "Действует с изменениями",
        "actual": False,
        "redcompleted": True,
        "hascontent": True,
        "redinitial": False,
    }
    values.update(overrides)
    return EbpiRedaction.model_validate(values)


def _build_service(redaction_html, redaction_content_nodes, redactions, document=None):
    tracked_repository = AsyncMock(spec=TrackedDocumentsRepository)
    tracked_repository.get_all_active.return_value = [document or _tracked_document()]

    changes_repository = AsyncMock(spec=LegalChangesRepository)
    changes_repository.get_known_redaction_ids.return_value = set()
    changes_repository.save_redaction_changes.side_effect = lambda data: len(data)

    client = AsyncMock(spec=PravoEbpiClient)
    client.get_redactions.return_value = redactions
    client.get_redaction_text.return_value = redaction_html
    client.get_redaction_content.return_value = redaction_content_nodes
    client.get_document_card_by_hash.return_value = EbpiDocumentCard.model_validate({
        "docid": 332268,
        "dochash": FZ_246_DOC_HASH,
        "docpassing": "Федеральный закон от 26.07.2026 № 246-ФЗ",
        "adoptions": [
            {"type": "Федеральный закон", "onumber": "246-ФЗ", "odate": "26.07.2026", "organ": ""}
        ],
    })

    service = MonitoringService(
        tracked_documents_repository=tracked_repository,
        legal_changes_repository=changes_repository,
        pravo_ebpi_client=client,
    )
    return service, tracked_repository, changes_repository, client


@pytest.mark.asyncio
async def test_creates_one_change_per_changed_section(redaction_html, redaction_content_nodes):
    service, _, changes_repository, _ = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()]
    )

    result = await service.run_monitoring()

    assert result.changes_created == 3
    saved = changes_repository.save_redaction_changes.await_args.kwargs["data"]
    assert sorted(item.section_number for item in saved) == ["57", "58", "59"]


@pytest.mark.asyncio
async def test_change_carries_official_effective_date(redaction_html, redaction_content_nodes):
    """Дата вступления в силу берётся из поля портала, а не из разбора текста поправки."""

    service, _, changes_repository, _ = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()]
    )

    await service.run_monitoring()

    saved = changes_repository.save_redaction_changes.await_args.kwargs["data"][0]
    assert saved.effective_date == date(2027, 3, 1)
    assert saved.redaction_date == date(2027, 3, 1)
    assert saved.ebpi_redaction_id == TK_RF_REDACTION_ID
    assert saved.amending_doc_hash == FZ_246_DOC_HASH
    assert saved.amending_act_type == "Федеральный закон"
    assert saved.amending_act_number == "246-ФЗ"
    assert saved.amending_act_date == date(2026, 7, 26)


@pytest.mark.asyncio
async def test_known_redaction_is_not_processed_again(redaction_html, redaction_content_nodes):
    """Повторный запуск не должен ни скачивать редакцию, ни создавать события."""

    service, _, changes_repository, client = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()]
    )
    changes_repository.get_known_redaction_ids.return_value = {TK_RF_REDACTION_ID}

    result = await service.run_monitoring()

    assert result.changes_created == 0
    client.get_redaction_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_redactions_before_monitor_from_are_ignored(redaction_html, redaction_content_nodes):
    """Постановка кодекса на контроль не должна порождать события по всей его истории."""

    service, _, changes_repository, client = _build_service(
        redaction_html,
        redaction_content_nodes,
        [_redaction(reddate="20240101")],
    )

    result = await service.run_monitoring()

    assert result.changes_created == 0
    client.get_redaction_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_redaction_is_not_a_change(redaction_html, redaction_content_nodes):
    service, _, _, client = _build_service(
        redaction_html,
        redaction_content_nodes,
        [_redaction(redinitial=True)],
    )

    result = await service.run_monitoring()

    assert result.changes_created == 0
    client.get_redaction_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_redaction_is_postponed(redaction_html, redaction_content_nodes):
    """Портал публикует редакцию раньше, чем готовит её текст."""

    service, _, changes_repository, client = _build_service(
        redaction_html,
        redaction_content_nodes,
        [_redaction(redcompleted=False)],
    )

    result = await service.run_monitoring()

    assert result.changes_created == 0
    assert result.items[0].redactions_skipped_incomplete == 1
    client.get_redaction_text.assert_not_awaited()
    changes_repository.save_redaction_changes.assert_not_awaited()


@pytest.mark.asyncio
async def test_caption_without_amending_law_creates_nothing(
    redaction_html, redaction_content_nodes
):
    service, _, changes_repository, _ = _build_service(
        redaction_html,
        redaction_content_nodes,
        [_redaction(redcaption="199. на 01.03.2027, редакция")],
    )

    result = await service.run_monitoring()

    assert result.changes_created == 0
    changes_repository.save_redaction_changes.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolves_document_hash_once_and_persists_it(
    redaction_html, redaction_content_nodes, search_payload
):
    document = _tracked_document(ebpi_doc_hash=None, ebpi_doc_id=None)
    service, tracked_repository, _, client = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()], document=document
    )
    client.search_documents.return_value = [
        EbpiSearchResult.model_validate(search_payload["docs"][0])
    ]
    tracked_repository.update.return_value = _tracked_document()

    await service.run_monitoring()

    tracked_repository.update.assert_awaited_once()
    assert tracked_repository.update.await_args.kwargs["values"]["ebpi_doc_hash"] == TK_RF_DOC_HASH


@pytest.mark.asyncio
async def test_resolves_law_registered_with_act_type_as_short_name(
    redaction_html, redaction_content_nodes
):
    """Регистрация из RAG должна находить нужный акт среди актов с одинаковым номером."""

    title = "О социальной защите инвалидов в Российской Федерации"
    document = _tracked_document(
        document_id="fz-181-1995",
        short_name="Федеральный закон",
        full_title=title,
        category="federal_law",
        document_number="181-ФЗ",
        adoption_date=date(1995, 11, 24),
        ebpi_doc_hash=None,
        ebpi_doc_id=None,
    )
    candidates = [
        {
            "docid": 28334,
            "dochash": "a" * 64,
            "docnames": title,
            "docpass0date": "19951124",
        },
        {
            "docid": 212847,
            "dochash": "b" * 64,
            "docnames": "О внесении изменений в Федеральный закон",
            "docpass0date": "20180703",
        },
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.url.params["q"])
        words = next(item["Words"] for item in query if item["AttrId"] == 7)
        matches = [
            candidate for candidate in candidates
            if all(word.removeprefix("+") in candidate["docnames"].lower() for word in words)
        ]
        return httpx.Response(200, json={"docs": matches})

    service, tracked_repository, _, _ = _build_service(
        redaction_html, redaction_content_nodes, [], document=document
    )
    tracked_repository.update.return_value = document
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        service.pravo_ebpi_client = PravoEbpiClient(
            httpx_client=http_client,
            settings=PravoEbpiSettings(_env_file=None, pravo_ebpi_base_url="http://portal.test"),
        )

        await service._ensure_document_hash(document=document)

    tracked_repository.update.assert_awaited_once_with(
        document=document,
        values={"ebpi_doc_hash": "a" * 64, "ebpi_doc_id": 28334},
    )


@pytest.mark.asyncio
async def test_document_with_wrong_adoption_date_is_not_matched(
    redaction_html, redaction_content_nodes, search_payload
):
    """Одного номера мало: он повторяется в разные годы, дата обязательна."""

    document = _tracked_document(ebpi_doc_hash=None, adoption_date=date(2020, 1, 1))
    service, _, _, client = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()], document=document
    )
    client.search_documents.return_value = [
        EbpiSearchResult.model_validate(search_payload["docs"][0])
    ]

    result = await service.run_monitoring()

    assert result.documents_failed == 1
    assert isinstance(result.items[0].error, str)


@pytest.mark.asyncio
async def test_failure_of_one_document_does_not_stop_others(
    redaction_html, redaction_content_nodes
):
    broken = _tracked_document(id=1, document_id="broken")
    healthy = _tracked_document(id=2, document_id="labor_code_rf")
    service, tracked_repository, _, client = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()]
    )
    tracked_repository.get_all_active.return_value = [broken, healthy]
    client.get_redactions.side_effect = [
        PravoEbpiRequestError("портал недоступен"),
        [_redaction()],
    ]

    result = await service.run_monitoring()

    assert result.documents_checked == 2
    assert result.documents_failed == 1
    assert result.changes_created == 3


@pytest.mark.asyncio
async def test_missing_document_in_bank_is_reported(redaction_html, redaction_content_nodes):
    document = _tracked_document(ebpi_doc_hash=None)
    service, _, _, client = _build_service(
        redaction_html, redaction_content_nodes, [_redaction()], document=document
    )
    client.search_documents.return_value = []

    result = await service.run_monitoring()

    assert result.documents_failed == 1
    assert "197-ФЗ" in result.items[0].error
