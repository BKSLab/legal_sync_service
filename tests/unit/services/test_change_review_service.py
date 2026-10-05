from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.exceptions.redaction import RedactionNotReadyError, RedactionSectionNotFoundError
from app.schemas.pravo_ebpi import EbpiRedaction
from app.services.change_review import compare_article, official_document_url
from app.services.section_text import SectionTextService


def redaction(redid, date, caption, **extra):
    return EbpiRedaction(redid=redid, reddate=date, redcaption=caption, redcompleted=True, **extra)


async def test_comparison_uses_previous_portal_order_not_largest_id():
    source = AsyncMock(spec=PravoEbpiClient)
    source.get_redactions.return_value = [
        redaction(50, "20270301", "3. Новая редакция"),
        redaction(900, "20270301", "2. Предыдущая редакция"),
        redaction(999, "20270201", "1. Исходная редакция"),
    ]
    text_service = SectionTextService(source)
    text_service._build_document = AsyncMock(side_effect=[
        Mock(extract_section_text=Mock(return_value="Заголовок\nНовый абзац\nДобавленный абзац")),
        Mock(extract_section_text=Mock(return_value="Заголовок\nСтарый абзац")),
    ])
    change = SimpleNamespace(ebpi_redaction_id=50, section_number="59", tracked_document=SimpleNamespace(ebpi_doc_hash="hash"))
    result = await compare_article(change, text_service)
    assert result.previous.redaction_id == 900
    assert result.passages[0].before == "Старый абзац"
    assert result.passages[0].after == "Новый абзац\nДобавленный абзац"
    assert [call.args[0] for call in text_service._build_document.await_args_list] == [50, 900]


async def test_missing_previous_redaction_is_not_reported_as_a_new_article():
    source = AsyncMock(spec=PravoEbpiClient)
    source.get_redactions.return_value = [redaction(50, "20270301", "1. Редакция")]
    text_service = SectionTextService(source)
    text_service._build_document = AsyncMock(return_value=Mock(extract_section_text=Mock(return_value="Текст")))
    result = await compare_article(SimpleNamespace(
        ebpi_redaction_id=50, section_number="59", tracked_document=SimpleNamespace(ebpi_doc_hash="hash"),
    ), text_service)
    assert result.previous is result.before is None
    assert not result.new_article
    assert result.passages == []


async def test_new_article_is_distinguished_from_incomplete_previous_redaction():
    source = AsyncMock(spec=PravoEbpiClient)
    previous = redaction(49, "20270201", "1. Редакция")
    source.get_redactions.return_value = [previous, redaction(50, "20270301", "2. Редакция")]
    text_service = SectionTextService(source)
    text_service._build_document = AsyncMock(side_effect=[
        Mock(extract_section_text=Mock(return_value="Новая статья")),
        Mock(extract_section_text=Mock(side_effect=RedactionSectionNotFoundError("59"))),
    ])
    change = SimpleNamespace(ebpi_redaction_id=50, section_number="59", tracked_document=SimpleNamespace(ebpi_doc_hash="hash"))
    result = await compare_article(change, text_service)
    assert result.new_article and result.before == ""
    previous.is_completed = False
    with pytest.raises(RedactionNotReadyError):
        await compare_article(change, text_service)


@pytest.mark.parametrize("reference", ["javascript:alert(1)", "https://example.org/document/0001202607260006", "от 26.07.2026 № 246-ФЗ"])
def test_official_link_does_not_turn_arbitrary_references_into_links(reference):
    assert official_document_url(None, reference) is None


def test_official_links_use_known_identifiers():
    assert official_document_url("a" * 64).endswith("#hash=" + "a" * 64 + "&ttl=3")
    assert official_document_url(None, "0001202607260006") == "http://publication.pravo.gov.ru/document/0001202607260006"
