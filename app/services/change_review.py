"""Официальные источники и сравнение редакций для ручной проверки события."""

import asyncio
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlencode, urlsplit

from app.db.models.legal_changes import LegalChange
from app.exceptions.redaction import (
    RedactionNotReadyError,
    RedactionParseError,
    RedactionSectionNotFoundError,
)
from app.schemas.pravo_ebpi import EbpiRedaction
from app.services.section_text import SectionTextService


def official_document_url(document_hash: str | None, reference: str = "") -> str | None:
    """Ссылка на конкретный акт; произвольные URL из данных не исполняются."""
    if document_hash and re.fullmatch(r"[0-9a-fA-F]{64}", document_hash):
        return "http://actual.pravo.gov.ru/content/content.html#" + urlencode({
            "hash": document_hash, "ttl": 3,
        })
    publication_number = reference.strip()
    if not re.fullmatch(r"\d{16}", publication_number):
        try:
            parsed = urlsplit(publication_number)
            if parsed.scheme not in ("http", "https") or parsed.hostname != "publication.pravo.gov.ru":
                return None
            publication_number = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        except ValueError:
            return None
    if re.fullmatch(r"\d{16}", publication_number):
        return f"http://publication.pravo.gov.ru/document/{publication_number}"
    return None


def redaction_order(redaction: EbpiRedaction):
    """Дата и порядковый номер портала; числовой ID не задаёт хронологию."""
    position = re.match(r"^\s*(\d+)\.", redaction.caption or "")
    return redaction.redaction_date, int(position.group(1)) if position else 0


@dataclass(frozen=True)
class ChangedPassage:
    before: str
    after: str


@dataclass(frozen=True)
class ArticleComparison:
    current: EbpiRedaction
    previous: EbpiRedaction | None
    before: str | None
    after: str
    passages: list[ChangedPassage]
    new_article: bool = False


def changed_passages(before: str, after: str) -> list[ChangedPassage]:
    old, new = before.splitlines(), after.splitlines()
    return [
        ChangedPassage("\n".join(old[start:end]), "\n".join(new[new_start:new_end]))
        for tag, start, end, new_start, new_end in SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        if tag != "equal"
    ]


async def compare_article(change: LegalChange, section_text: SectionTextService) -> ArticleComparison:
    """Читает точную редакцию события и её предшественницу, не меняя событие."""
    document_hash = change.tracked_document.ebpi_doc_hash
    if not document_hash or change.ebpi_redaction_id is None:
        raise RedactionParseError("Для сравнения нужны документ в источнике и редакция события.")
    redactions = sorted(
        await section_text.pravo_ebpi_client.get_redactions(document_hash=document_hash),
        key=redaction_order,
    )
    position = next((i for i, item in enumerate(redactions) if item.redaction_id == change.ebpi_redaction_id), None)
    if position is None:
        raise RedactionParseError("Редакция события отсутствует в списке редакций источника.")
    current = redactions[position]
    previous = redactions[position - 1] if position else None
    if not current.is_completed or (previous and not previous.is_completed):
        raise RedactionNotReadyError("Портал ещё готовит текст одной из редакций. Повторите сравнение позже.")
    parsed = await section_text._build_document(current.redaction_id)
    after = parsed.extract_section_text(change.section_number)
    before, new_article = None, False
    if previous:
        parsed_previous = await section_text._build_document(previous.redaction_id)
        try:
            before = parsed_previous.extract_section_text(change.section_number)
        except RedactionSectionNotFoundError:
            before, new_article = "", True
    passages = await asyncio.to_thread(changed_passages, before, after) if before is not None else []
    return ArticleComparison(current, previous, before, after, passages, new_article)
