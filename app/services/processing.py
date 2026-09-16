import logging
from datetime import UTC, datetime

from app.clients.pravo_ebpi import PravoEbpiClient
from app.clients.rag import RagClient
from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.exceptions.rag import RagStaleRevisionError
from app.exceptions.redaction import RedactionNotReadyError, RedactionParseError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.monitoring import ProcessingResult
from app.services.redaction_parser import RedactionDocument
from app.services.section_text import SectionTextService

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
        delivery_enabled: bool = False,
    ):
        self.legal_changes_repository = legal_changes_repository
        self.pravo_ebpi_client = pravo_ebpi_client
        self.section_text = SectionTextService(pravo_ebpi_client)
        self.rag_client = rag_client
        self.max_retries = max_retries
        self.delivery_enabled = delivery_enabled

    # Блок публичных методов

    async def run_processing(self) -> ProcessingResult:
        """Отправляет в RAG Service все события, у которых наступил срок.

        Отказ по одному событию не влияет на остальные.

        Returns:
            Сводка запуска.
        """

        if not self.delivery_enabled:
            logger.info("Отправка в RAG отключена; очередь оставлена без изменений.")
            return ProcessingResult(
                delivery_disabled=True,
                changes_selected=0,
                changes_sent=0,
                changes_failed=0,
                changes_postponed=0,
            )

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
            except RedactionNotReadyError as error:
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

        redaction_text = await self.section_text.get_text(
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
                "consolidated_text_source": self.section_text.source_reference(change=change),
                "sent_at": datetime.now(tz=UTC),
                "rag_response": rag_response,
                "last_error": None,
            },
        )
        logger.info("✅ Событие отправлено в RAG. change_id=%s", change.id)

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
