import asyncio
import functools
import logging
import re
from datetime import date

from app.clients.pravo_ebpi import PravoEbpiClient
from app.db.models.tracked_documents import TrackedDocument
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.exceptions.pravo_ebpi import PravoEbpiClientError, PravoEbpiDocumentNotFoundError
from app.exceptions.redaction import RedactionParseError
from app.exceptions.tracked_documents import TrackedDocumentRepositoryError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.legal_changes import LegalChangeCreateRequest
from app.schemas.monitoring import MonitoringDocumentResult, MonitoringResult
from app.schemas.pravo_ebpi import EbpiRedaction
from app.services.redaction_parser import RedactionDocument

logger = logging.getLogger(__name__)


class MonitoringService:
    """Ежедневный мониторинг изменений отслеживаемых документов.

    Мониторинг не перебирает дневные публикации в поисках актов, затрагивающих
    отслеживаемые документы. Вместо этого он спрашивает у банка редакций список
    редакций каждого документа на контроле: появление новой редакции и есть
    факт изменения. Это один запрос на документ вместо перебора всех публикаций
    и не требует разбора текста акта-поправки.
    """

    # Реквизиты акта-поправки в подписи редакции: «(№ 246-ФЗ от 26.07.2026)».
    REDACTION_CAPTION_PATTERN = re.compile(
        r"№\s*(?P<number>[^\s,()]+)\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})"
    )

    def __init__(
        self,
        tracked_documents_repository: TrackedDocumentsRepository,
        legal_changes_repository: LegalChangesRepository,
        pravo_ebpi_client: PravoEbpiClient,
    ):
        self.tracked_documents_repository = tracked_documents_repository
        self.legal_changes_repository = legal_changes_repository
        self.pravo_ebpi_client = pravo_ebpi_client

    # Блок публичных методов

    async def run_monitoring(self) -> MonitoringResult:
        """Проверяет все документы на контроле и создаёт события изменений.

        Отказ по одному документу не прерывает обработку остальных: каждый
        документ обрабатывается независимо, а его ошибка попадает в результат.

        Returns:
            Сводка по каждому обработанному документу.
        """

        logger.info("🚀 Мониторинг изменений запущен.")
        documents = await self.tracked_documents_repository.get_all_active()
        results: list[MonitoringDocumentResult] = []

        for document in documents:
            try:
                result = await self._monitor_document(document=document)
            except (
                PravoEbpiClientError,
                RedactionParseError,
                TrackedDocumentRepositoryError,
                LegalChangeRepositoryError,
            ) as error:
                logger.error(
                    "❌ Мониторинг документа не выполнен. document_id=%s причина=%s",
                    document.document_id,
                    error,
                    exc_info=True,
                )
                result = MonitoringDocumentResult(
                    document_id=document.document_id,
                    error=str(error),
                )
            results.append(result)

        summary = MonitoringResult(
            documents_checked=len(results),
            changes_created=sum(item.changes_created for item in results),
            documents_failed=sum(1 for item in results if item.error),
            items=results,
        )
        logger.info(
            "✅ Мониторинг завершён. документов=%s событий=%s ошибок=%s",
            summary.documents_checked,
            summary.changes_created,
            summary.documents_failed,
        )
        return summary

    # Блок приватных методов обработки документа

    async def _monitor_document(self, document: TrackedDocument) -> MonitoringDocumentResult:
        """Обрабатывает один документ на контроле."""

        logger.info("🔍 Проверка документа. document_id=%s", document.document_id)
        document = await self._ensure_document_hash(document=document)
        redactions = await self.pravo_ebpi_client.get_redactions(
            document_hash=document.ebpi_doc_hash,
        )
        pending = await self._select_pending_redactions(document=document, redactions=redactions)

        changes_created = 0
        skipped_incomplete = 0
        for redaction in pending:
            if not redaction.is_completed:
                # Портал публикует редакцию раньше, чем готовит её текст.
                # Пропуск не теряет изменение: следующий запуск увидит ту же
                # редакцию снова, а событий по ней ещё не создано.
                logger.warning(
                    "⚠️ Текст редакции ещё не готов, отложено. document_id=%s redaction_id=%s дата=%s",
                    document.document_id,
                    redaction.redaction_id,
                    redaction.redaction_date,
                )
                skipped_incomplete += 1
                continue
            changes_created += await self._create_changes_for_redaction(
                document=document,
                redaction=redaction,
            )

        return MonitoringDocumentResult(
            document_id=document.document_id,
            redactions_total=len(redactions),
            redactions_pending=len(pending),
            redactions_skipped_incomplete=skipped_incomplete,
            changes_created=changes_created,
        )

    async def _ensure_document_hash(self, document: TrackedDocument) -> TrackedDocument:
        """Определяет идентификатор документа в банке редакций и сохраняет его.

        Поиск по реквизитам выполняется один раз за всё время наблюдения:
        идентификатор акта в банке стабилен.
        Наименование для поиска приходит из RAG в поле `full_title`.
        """

        if document.ebpi_doc_hash:
            return document

        logger.info(
            "🔍 Идентификатор документа в банке ещё не определён. document_id=%s",
            document.document_id,
        )
        candidates = await self.pravo_ebpi_client.search_documents(
            document_number=document.document_number,
            title_words=document.full_title,
        )
        matched = [
            candidate
            for candidate in candidates
            if candidate.adoption_date == document.adoption_date
        ]
        if not matched:
            raise PravoEbpiDocumentNotFoundError(
                search_key=f"{document.document_number} от {document.adoption_date}"
            )
        if len(matched) > 1:
            logger.warning(
                "⚠️ По реквизитам найдено несколько актов, взят первый. document_id=%s найдено=%s",
                document.document_id,
                len(matched),
            )

        found = matched[0]
        logger.info(
            "✅ Документ найден в банке редакций. document_id=%s doc_hash=%s",
            document.document_id,
            found.doc_hash,
        )
        return await self.tracked_documents_repository.update(
            document=document,
            values={"ebpi_doc_hash": found.doc_hash, "ebpi_doc_id": found.doc_id},
        )

    async def _select_pending_redactions(
        self,
        document: TrackedDocument,
        redactions: list[EbpiRedaction],
    ) -> list[EbpiRedaction]:
        """Отбирает редакции, по которым события ещё не создавались.

        Отсекаются редакции старше даты постановки на контроль и исходная
        редакция акта: они описывают не изменение, а состояние документа на
        момент, когда мониторинг ещё не вёлся.
        """

        known_ids = await self.legal_changes_repository.get_known_redaction_ids(
            tracked_document_id=document.id,
        )
        pending = [
            redaction
            for redaction in redactions
            if not redaction.is_initial
            and redaction.redaction_date >= document.monitor_from
            and redaction.redaction_id not in known_ids
        ]
        pending.sort(key=lambda redaction: redaction.redaction_date)
        return pending

    async def _create_changes_for_redaction(
        self,
        document: TrackedDocument,
        redaction: EbpiRedaction,
    ) -> int:
        """Создаёт события изменений по одной редакции документа."""

        citation = self._parse_amending_citation(caption=redaction.caption)
        if citation is None:
            logger.warning(
                "⚠️ В подписи редакции нет реквизитов акта-поправки. document_id=%s redaction_id=%s",
                document.document_id,
                redaction.redaction_id,
            )
            return 0

        parsed_redaction = await self._build_redaction_document(redaction_id=redaction.redaction_id)
        amending_hash = parsed_redaction.find_amending_document_hash(citation=citation)
        if amending_hash is None:
            logger.warning(
                "⚠️ Акт-поправка не найден в тексте редакции. document_id=%s redaction_id=%s акт=%s",
                document.document_id,
                redaction.redaction_id,
                citation,
            )
            return 0

        # Вид акта-поправки берётся из его карточки: подпись редакции содержит
        # только номер и дату, а выводить вид из номера означало бы угадывать.
        amending_act = await self._get_amending_act(document_hash=amending_hash)

        changed_sections = parsed_redaction.find_sections_changed_by(
            amending_document_hash=amending_hash,
        )
        if not changed_sections:
            logger.warning(
                "⚠️ Изменённых статей не найдено. document_id=%s redaction_id=%s акт=%s",
                document.document_id,
                redaction.redaction_id,
                citation,
            )
            return 0

        changes = []
        for section in changed_sections:
            change = LegalChangeCreateRequest(
                tracked_document_id=document.id,
                section_number=section.number,
                section_title=section.title,
                amending_law_ref=citation,
                amending_doc_hash=amending_hash,
                amending_act_type=amending_act.act_type if amending_act else None,
                amending_act_number=amending_act.act_number if amending_act else None,
                amending_act_date=amending_act.act_date if amending_act else None,
                ebpi_redaction_id=redaction.redaction_id,
                redaction_date=redaction.redaction_date,
                effective_date=redaction.redaction_date,
                change_description=redaction.caption,
            )
            changes.append(change)

        created = await self.legal_changes_repository.save_redaction_changes(data=changes)

        logger.info(
            "💾 События изменений созданы. document_id=%s redaction_id=%s статей=%s новых=%s",
            document.document_id,
            redaction.redaction_id,
            len(changed_sections),
            created,
        )
        return created

    async def _get_amending_act(self, document_hash: str):
        """Возвращает реквизиты акта-поправки в разобранном виде.

        Отсутствие карточки не отменяет создание событий: набор изменённых
        статей и дата вступления уже известны, а реквизиты нужны только для
        оформления ссылки на источник изменения.
        """

        try:
            card = await self.pravo_ebpi_client.get_document_card_by_hash(
                document_hash=document_hash,
            )
        except PravoEbpiClientError as error:
            logger.warning(
                "⚠️ Реквизиты акта-поправки не получены. doc_hash=%s причина=%s",
                document_hash,
                error,
            )
            return None
        return card.adoption

    async def _build_redaction_document(self, redaction_id: int) -> RedactionDocument:
        """Загружает и разбирает редакцию документа.

        Разбор кодекса — это сотни тысяч символов HTML, поэтому он уносится в
        пул потоков: иначе он заблокирует event loop на всё время работы.
        """

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

    def _parse_amending_citation(self, caption: str | None) -> str | None:
        """Приводит подпись редакции к виду ссылки на акт в тексте документа."""

        if not caption:
            return None
        match = self.REDACTION_CAPTION_PATTERN.search(caption)
        if match is None:
            return None
        return f"от {match.group('date')} № {match.group('number')}"

    @staticmethod
    def _today() -> date:
        """Возвращает текущую дату; вынесено для подмены в тестах."""

        return date.today()
