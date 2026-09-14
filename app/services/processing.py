import asyncio
import functools
import logging
from datetime import UTC, datetime

from app.clients.pravo_ebpi import PravoEbpiClient
from app.clients.rag import RagClient
from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.exceptions.rag import RagStaleRevisionError
from app.exceptions.redaction import RedactionParseError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.monitoring import ProcessingResult
from app.services.redaction_parser import RedactionDocument

logger = logging.getLogger(__name__)


class ProcessingService:
    """Отправка подтверждённых изменений в RAG Service в дату вступления в силу.

    Текст статьи берётся из той редакции, которая порождена актом-поправкой, —
    именно она вступает в силу в `send_at`. Брать «самую свежую редакцию на
    момент запроса» нельзя: к моменту отправки на портале может уже лежать
    более поздняя редакция с ещё не вступившими в силу изменениями.
    """

    def __init__(
        self,
        legal_changes_repository: LegalChangesRepository,
        pravo_ebpi_client: PravoEbpiClient,
        rag_client: RagClient,
        max_retries: int,
    ):
        self.legal_changes_repository = legal_changes_repository
        self.pravo_ebpi_client = pravo_ebpi_client
        self.rag_client = rag_client
        self.max_retries = max_retries

    # Блок публичных методов

    async def run_processing(self) -> ProcessingResult:
        """Отправляет в RAG Service все события, у которых наступил срок.

        Отказ по одному событию не влияет на остальные.

        Returns:
            Сводка запуска.
        """

        async with self.legal_changes_repository.processing_lock() as acquired:
            if not acquired:
                logger.info("ℹ️ Очередь уже обрабатывается другим запуском.")
                return ProcessingResult(
                    changes_selected=0,
                    changes_sent=0,
                    changes_failed=0,
                    changes_postponed=0,
                    already_running=True,
                )
            recovered = await self.legal_changes_repository.recover_interrupted_processing()
            result = await self._run_processing()
            result.changes_recovered = recovered
            return result

    async def _run_processing(self) -> ProcessingResult:
        """Обрабатывает очередь при уже захваченной общей блокировке."""

        logger.info("🚀 Обработка очереди отправки запущена.")
        changes = await self.legal_changes_repository.get_due_for_sending(
            now=datetime.now(tz=UTC),
            max_retries=self.max_retries,
        )
        # Одна редакция обычно меняет несколько статей одного документа. Её
        # текст — до двух мегабайт, поэтому разбор кэшируется на время запуска.
        parsed_redactions: dict[int, RedactionDocument] = {}
        sent = 0
        failed = 0
        postponed = 0
        superseded = 0

        for change in changes:
            try:
                await self._process_change(change=change, parsed_redactions=parsed_redactions)
                sent += 1
            except _RedactionNotReadyError as error:
                # Событие уже переведено в `processing`, но отправки не было.
                # Без возврата в `scheduled` оно выпало бы из выборки навсегда.
                logger.warning(
                    "⚠️ Отправка отложена: %s. change_id=%s",
                    error,
                    change.id,
                )
                await self.legal_changes_repository.update(
                    change=change,
                    values={"status": LegalChangeStatus.SCHEDULED},
                )
                postponed += 1
            except RagStaleRevisionError as error:
                await self.legal_changes_repository.update(
                    change=change,
                    values={
                        "status": LegalChangeStatus.CANCELLED,
                        "rag_response": error.response,
                        "last_error": str(error)[:2000],
                    },
                )
                superseded += 1
            except LegalChangeRepositoryError:
                # При недоступной БД нельзя надёжно записать исход. Прерываем
                # запуск; следующий владелец блокировки восстановит событие.
                raise
            except Exception as error:
                # Неожиданные ошибки также получают повтор. CancelledError
                # сюда не входит: остановленный запуск восстановится по БД.
                await self._mark_failed(change=change, error=error)
                failed += 1

        logger.info(
            "✅ Обработка очереди завершена. отобрано=%s отправлено=%s ошибок=%s отложено=%s устарело=%s",
            len(changes),
            sent,
            failed,
            postponed,
            superseded,
        )
        return ProcessingResult(
            changes_selected=len(changes),
            changes_sent=sent,
            changes_failed=failed,
            changes_postponed=postponed,
            changes_superseded=superseded,
        )

    # Блок приватных методов обработки события

    async def _process_change(
        self,
        change: LegalChange,
        parsed_redactions: dict[int, RedactionDocument],
    ) -> None:
        """Отправляет одно событие изменения в RAG Service."""

        # Связь читается до первого обновления: после `refresh()` она может
        # оказаться незагруженной, а ленивая загрузка в async-коде упадёт.
        document = change.tracked_document
        logger.info(
            "🔄 Обработка события. change_id=%s document_id=%s section=%s",
            change.id,
            document.document_id,
            change.section_number,
        )
        await self.legal_changes_repository.update(
            change=change,
            values={"status": LegalChangeStatus.PROCESSING},
        )

        redaction_text = await self._get_section_text(
            change=change,
            document_hash=document.ebpi_doc_hash,
            parsed_redactions=parsed_redactions,
        )
        revision_date = change.redaction_date or change.effective_date
        if revision_date is None:
            raise RedactionParseError(f"У события {change.id} не определена дата редакции.")

        rag_response = await self.rag_client.update_section(
            document_id=document.document_id,
            section_number=change.section_number,
            category=document.category,
            raw_text=redaction_text,
            section_title=change.section_title or document.short_name,
            revision_date=revision_date,
            audience=change.audience_override or document.audience,
            source_title=change.source_title_override or document.source_title,
            topics=change.topics_override if change.topics_override is not None else document.topics,
            amending_act_type=change.amending_act_type,
            amending_act_number=change.amending_act_number,
            amending_act_date=change.amending_act_date,
        )

        await self.legal_changes_repository.update(
            change=change,
            values={
                "status": LegalChangeStatus.SENT,
                "consolidated_text": redaction_text,
                "consolidated_text_source": self._build_source_reference(change=change),
                "sent_at": datetime.now(tz=UTC),
                "rag_response": rag_response,
                "last_error": None,
            },
        )
        logger.info("✅ Событие отправлено в RAG. change_id=%s", change.id)

    async def _get_section_text(
        self,
        change: LegalChange,
        document_hash: str | None,
        parsed_redactions: dict[int, RedactionDocument],
    ) -> str:
        """Извлекает текст статьи из нужной редакции документа."""

        if change.ebpi_redaction_id is None:
            raise RedactionParseError(
                f"У события {change.id} не указана редакция документа."
            )

        parsed = parsed_redactions.get(change.ebpi_redaction_id)
        if parsed is None:
            await self._ensure_redaction_ready(change=change, document_hash=document_hash)
            parsed = await self._build_redaction_document(redaction_id=change.ebpi_redaction_id)
            parsed_redactions[change.ebpi_redaction_id] = parsed
        return parsed.extract_section_text(section_number=change.section_number)

    async def _ensure_redaction_ready(self, change: LegalChange, document_hash: str | None) -> None:
        """Проверяет, что портал завершил подготовку текста редакции.

        Незавершённая редакция отдаётся порталом частично. Отправить такой
        текст в RAG хуже, чем опоздать: подмену полного текста статьи
        обрезанным потом никто не заметит.
        """

        if not document_hash:
            return
        redactions = await self.pravo_ebpi_client.get_redactions(document_hash=document_hash)
        for redaction in redactions:
            if redaction.redaction_id != change.ebpi_redaction_id:
                continue
            if not redaction.is_completed:
                raise _RedactionNotReadyError(
                    f"текст редакции {redaction.redaction_id} ещё готовится порталом"
                )
            return

    async def _build_redaction_document(self, redaction_id: int) -> RedactionDocument:
        """Загружает и разбирает редакцию вне event loop."""

        redaction_html = await self.pravo_ebpi_client.get_redaction_text(redaction_id=redaction_id)
        content_nodes = await self.pravo_ebpi_client.get_redaction_content(redaction_id=redaction_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(
                RedactionDocument,
                redaction_html=redaction_html,
                content_nodes=content_nodes,
            ),
        )

    async def _mark_failed(self, change: LegalChange, error: Exception) -> None:
        """Переводит событие в статус ошибки и сохраняет причину."""

        retry_count = change.retry_count + 1
        logger.error(
            "❌ Отправка события не выполнена. change_id=%s попытка=%s причина=%s",
            change.id,
            retry_count,
            error,
            exc_info=True,
        )
        await self.legal_changes_repository.update(
            change=change,
            values={
                "status": LegalChangeStatus.FAILED,
                "retry_count": retry_count,
                "last_error": str(error)[:2000],
            },
        )

    @staticmethod
    def _build_source_reference(change: LegalChange) -> str:
        """Формирует ссылку на источник консолидированного текста."""

        return (
            "actual.pravo.gov.ru: редакция "
            f"{change.ebpi_redaction_id} от {change.redaction_date}"
        )


class _RedactionNotReadyError(Exception):
    """Портал ещё не завершил подготовку текста редакции.

    Это не отказ, а причина отложить отправку: следующий запуск повторит
    попытку, а счётчик неудач события не растёт.
    """
