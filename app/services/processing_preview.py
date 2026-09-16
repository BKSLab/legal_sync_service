import logging

from app.db.models.legal_changes import LegalChangeStatus
from app.exceptions.legal_changes import (
    LegalChangeNotFoundError,
    LegalChangePreviewConflictError,
)
from app.exceptions.redaction import RedactionNotReadyError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.processing_preview import ProcessingPreviewRequest, ProcessingPreviewResult
from app.services.section_text import SectionTextService

logger = logging.getLogger(__name__)


class ProcessingPreviewService:
    """Проверяет дату и извлекает статью; клиент RAG этому сервису не передаётся."""

    def __init__(self, repository: LegalChangesRepository, section_text: SectionTextService):
        self.repository = repository
        self.section_text = section_text

    async def preview_change(
        self, change_id: int, data: ProcessingPreviewRequest,
    ) -> ProcessingPreviewResult:
        # Та же блокировка, что при реальной отправке. Восстановление очереди
        # здесь не выполняется: пробный запуск не изменяет статусы событий.
        async with self.repository.processing_lock() as acquired:
            if not acquired:
                raise LegalChangePreviewConflictError("Очередь занята. Повторите пробный запуск позже.")
            change = await self.repository.get_by_id(change_id)
            if change is None:
                raise LegalChangeNotFoundError(change_id)
            if change.status in (
                LegalChangeStatus.PROCESSING, LegalChangeStatus.SENT, LegalChangeStatus.CANCELLED,
            ):
                raise LegalChangePreviewConflictError(
                    "Пробный запуск доступен для ожидающих проверки, запланированных событий и ошибок."
                )
            if change.send_at is None:
                raise LegalChangePreviewConflictError("У события не задан срок обработки.")

            result = ProcessingPreviewResult(
                change_id=change.id, as_of=data.as_of, send_at=change.send_at,
                event_status=change.status, section_number=change.section_number,
                redaction_id=change.ebpi_redaction_id, redaction_date=change.redaction_date,
                outcome="not_due", message="На выбранный момент срок события ещё не наступил.",
            )
            logger.info("Пробная обработка события. change_id=%s as_of=%s", change.id, data.as_of)
            if data.as_of < change.send_at:
                logger.info("Срок события ещё не наступил. change_id=%s", change.id)
                return result

            try:
                extracted_text = await self.section_text.get_text(
                    change=change, document_hash=change.tracked_document.ebpi_doc_hash,
                    parsed_redactions={},
                )
            except RedactionNotReadyError as error:
                result.outcome = "redaction_not_ready"
                result.message = str(error)
                logger.info("Пробное извлечение отложено. change_id=%s причина=%s", change.id, error)
                return result

            source = self.section_text.source_reference(change)
            if not await self.repository.save_preview_text(change, extracted_text, source):
                raise LegalChangePreviewConflictError(
                    "Событие изменилось во время загрузки. Обновите карточку и повторите проверку."
                )
            result.outcome = "extracted"
            result.message = "Статья извлечена и сохранена в карточке события. Отправка не выполнялась."
            result.consolidated_text = extracted_text
            result.consolidated_text_source = source
            result.text_length = len(extracted_text)
            logger.info(
                "Пробное извлечение выполнено. change_id=%s редакция=%s статья=%s символов=%s",
                result.change_id, result.redaction_id, result.section_number, result.text_length,
            )
            return result
