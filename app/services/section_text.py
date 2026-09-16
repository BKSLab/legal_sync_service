import asyncio
import functools

from app.clients.pravo_ebpi import PravoEbpiClient
from app.db.models.legal_changes import LegalChange
from app.exceptions.redaction import RedactionNotReadyError, RedactionParseError
from app.services.redaction_parser import RedactionDocument


class SectionTextService:
    """Общее извлечение статьи для пробного запуска и реальной отправки."""

    def __init__(self, pravo_ebpi_client: PravoEbpiClient):
        self.pravo_ebpi_client = pravo_ebpi_client

    async def get_text(
        self,
        change: LegalChange,
        document_hash: str | None,
        parsed_redactions: dict[int, RedactionDocument],
    ) -> str:
        """Берёт статью из редакции события, включая ещё не вступившую в силу."""

        if change.ebpi_redaction_id is None:
            raise RedactionParseError(f"У события {change.id} не указана редакция документа.")
        parsed = parsed_redactions.get(change.ebpi_redaction_id)
        if parsed is None:
            await self._ensure_ready(change, document_hash)
            parsed = await self._build_document(change.ebpi_redaction_id)
            parsed_redactions[change.ebpi_redaction_id] = parsed
        return parsed.extract_section_text(section_number=change.section_number)

    async def _ensure_ready(self, change: LegalChange, document_hash: str | None) -> None:
        if not document_hash:
            raise RedactionParseError(f"У документа события {change.id} не определён источник.")
        redactions = await self.pravo_ebpi_client.get_redactions(document_hash=document_hash)
        for redaction in redactions:
            if redaction.redaction_id != change.ebpi_redaction_id:
                continue
            if not redaction.is_completed:
                raise RedactionNotReadyError(
                    f"Текст редакции {redaction.redaction_id} ещё готовится порталом."
                )
            return
        raise RedactionParseError(
            f"Редакция {change.ebpi_redaction_id} отсутствует в списке редакций документа."
        )

    async def _build_document(self, redaction_id: int) -> RedactionDocument:
        redaction_html = await self.pravo_ebpi_client.get_redaction_text(redaction_id=redaction_id)
        content_nodes = await self.pravo_ebpi_client.get_redaction_content(redaction_id=redaction_id)
        return await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                RedactionDocument, redaction_html=redaction_html, content_nodes=content_nodes,
            ),
        )

    @staticmethod
    def source_reference(change: LegalChange) -> str:
        return (
            "actual.pravo.gov.ru: редакция "
            f"{change.ebpi_redaction_id} от {change.redaction_date}"
        )
