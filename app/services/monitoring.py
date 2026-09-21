import asyncio
import functools
import logging
import re
from datetime import date

from app.clients.pravo_ebpi import PravoEbpiClient
from app.db.models.tracked_documents import TrackedDocument
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.exceptions.monitoring import MonitoringJournalError
from app.exceptions.pravo_ebpi import PravoEbpiClientError, PravoEbpiDocumentNotFoundError
from app.exceptions.redaction import RedactionParseError
from app.exceptions.tracked_documents import TrackedDocumentRepositoryError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.monitoring import MonitoringJournal, MonitoringRunRecorder
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.legal_changes import LegalChangeCreateRequest
from app.schemas.monitoring import MonitoringDocumentResult, MonitoringResult
from app.schemas.pravo_ebpi import EbpiRedaction
from app.services.redaction_parser import RedactionDocument

logger = logging.getLogger(__name__)


class MonitoringService:
    """Периодический мониторинг изменений отслеживаемых документов.

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
        journal: MonitoringJournal | None = None,
    ):
        self.tracked_documents_repository = tracked_documents_repository
        self.legal_changes_repository = legal_changes_repository
        self.pravo_ebpi_client = pravo_ebpi_client
        self.journal = journal
        self.recorder: MonitoringRunRecorder | None = None

    # Блок публичных методов

    async def run_monitoring(self, source: str = "manual") -> MonitoringResult:
        if self.journal is None:
            return await self._run_monitoring()
        async with self.journal.start(source) as recorder:
            if recorder.skipped:
                return MonitoringResult(
                    run_id=recorder.run_id, already_running=True,
                    documents_checked=0, changes_created=0, documents_failed=0, items=[],
                )
            self.recorder = recorder
            previous_observer = getattr(self.pravo_ebpi_client, "request_observer", None)
            self.pravo_ebpi_client.request_observer = self._record
            try:
                result = await self._run_monitoring()
                await recorder.finish(result)
                return result
            finally:
                self.recorder = None
                self.pravo_ebpi_client.request_observer = previous_observer

    async def _record(self, stage: str, message: str, details: dict | None = None, level: str = "info") -> None:
        if self.recorder is not None:
            logger.log(
                getattr(logging, level.upper()), "Мониторинг run_id=%s check_id=%s stage=%s: %s",
                self.recorder.run_id, self.recorder.check_id, stage, message,
            )
            await self.recorder.event(stage, message, details, level)

    async def _run_monitoring(self) -> MonitoringResult:
        """Проверяет все документы на контроле и создаёт события изменений.

        Отказ по одному документу не прерывает обработку остальных: каждый
        документ обрабатывается независимо, а его ошибка попадает в результат.

        Returns:
            Сводка по каждому обработанному документу.
        """

        logger.info("🚀 Мониторинг изменений запущен.")
        documents = await self.tracked_documents_repository.get_all_active()
        if self.recorder:
            await self.recorder.set_total(len(documents))
        results: list[MonitoringDocumentResult] = []

        for document in documents:
            result = MonitoringDocumentResult(document_id=document.document_id)
            if self.recorder:
                await self.recorder.begin_document(document, result)
            try:
                await self._monitor_document(document=document, result=result)
            except MonitoringJournalError:
                raise
            except Exception as error:
                logger.error(
                    "❌ Мониторинг документа не выполнен. document_id=%s причина=%s",
                    result.document_id,
                    error,
                    exc_info=True,
                )
                result.error = f"{type(error).__name__}: {error}"
                if self.recorder:
                    await self.recorder.finish_document(error)
                if not isinstance(error, PravoEbpiClientError | RedactionParseError | TrackedDocumentRepositoryError | LegalChangeRepositoryError):
                    raise
            else:
                if self.recorder:
                    await self.recorder.finish_document()
            results.append(result)

        summary = MonitoringResult(
            run_id=self.recorder.run_id if self.recorder else None,
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

    async def _monitor_document(self, document: TrackedDocument, result: MonitoringDocumentResult) -> MonitoringDocumentResult:
        """Обрабатывает один документ на контроле."""

        logger.info("🔍 Проверка документа. document_id=%s", document.document_id)
        document = await self._ensure_document_hash(document=document)
        redactions = await self.pravo_ebpi_client.get_redactions(
            document_hash=document.ebpi_doc_hash,
        )
        result.redactions_total = len(redactions)
        ordered = sorted(redactions, key=self._redaction_order)
        previous_by_id = {
            current.redaction_id: previous
            for previous, current in zip(ordered, ordered[1:], strict=False)
        }
        pending = await self._select_pending_redactions(document=document, redactions=redactions)
        result.redactions_pending = len(pending)
        await self._record("redactions_selected", "Редакции получены и отобраны для проверки.", {
            "total": len(redactions), "pending": len(pending), "monitor_from": document.monitor_from.isoformat(),
            "pending_redactions": [{"id": item.redaction_id, "date": item.redaction_date.isoformat(), "caption": item.caption} for item in pending],
        })
        for redaction in pending:
            await self._record("redaction_started", "Проверка редакции.", {
                "redaction_id": redaction.redaction_id, "redaction_date": redaction.redaction_date.isoformat(), "caption": redaction.caption,
            })
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
                result.redactions_skipped_incomplete += 1
                await self._record("redaction_incomplete", "Текст редакции ещё не готов. Повторим в следующем запуске.", {"redaction_id": redaction.redaction_id}, "warning")
                continue
            previous = previous_by_id.get(redaction.redaction_id)
            if previous is not None and not previous.is_completed:
                logger.warning(
                    "⚠️ Предыдущая редакция ещё не готова для сравнения, отложено. "
                    "document_id=%s redaction_id=%s previous_redaction_id=%s",
                    document.document_id, redaction.redaction_id, previous.redaction_id,
                )
                result.redactions_skipped_incomplete += 1
                await self._record("previous_incomplete", "Предыдущая редакция ещё не готова для сравнения. Проверка отложена.", {
                    "redaction_id": redaction.redaction_id, "previous_redaction_id": previous.redaction_id,
                }, "warning")
                continue
            await self._create_changes_for_redaction(
                document=document,
                redaction=redaction,
                progress=result,
                previous_redaction=previous,
            )
            await self._record("redaction_finished", "Проверка редакции завершена.", {"redaction_id": redaction.redaction_id})

        return result

    async def _ensure_document_hash(self, document: TrackedDocument) -> TrackedDocument:
        """Определяет идентификатор документа в банке редакций и сохраняет его.

        Поиск по реквизитам выполняется один раз за всё время наблюдения:
        идентификатор акта в банке стабилен.
        Наименование для поиска приходит из RAG в поле `full_title`.
        """

        if document.ebpi_doc_hash:
            await self._record("document_resolved", "Используется сохранённый идентификатор акта в банке редакций.", {"ebpi_doc_hash": document.ebpi_doc_hash})
            return document

        logger.info(
            "🔍 Идентификатор документа в банке ещё не определён. document_id=%s",
            document.document_id,
        )
        await self._record("document_search", "Поиск акта по реквизитам.", {
            "document_number": document.document_number, "title": document.full_title,
            "adoption_date": document.adoption_date.isoformat(),
        })
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
            await self._record("ambiguous_document", "Найдено несколько актов по реквизитам; выбран первый.", {
                "matches": [{"doc_hash": item.doc_hash, "doc_id": item.doc_id} for item in matched],
            }, "warning")
            logger.warning(
                "⚠️ По реквизитам найдено несколько актов, взят первый. document_id=%s найдено=%s",
                document.document_id,
                len(matched),
            )

        found = matched[0]
        await self._record("document_found", "Документ найден в банке редакций.", {"ebpi_doc_hash": found.doc_hash, "ebpi_doc_id": found.doc_id})
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
        await self._record("redactions_filtered", "Исключены исходная редакция, редакции до начала контроля и уже известные изменения.", {
            "initial": [item.redaction_id for item in redactions if item.is_initial],
            "before_monitor_from": [item.redaction_id for item in redactions if not item.is_initial and item.redaction_date < document.monitor_from],
            "already_known": [item.redaction_id for item in redactions if not item.is_initial and item.redaction_date >= document.monitor_from and item.redaction_id in known_ids],
        })
        pending = [
            redaction
            for redaction in redactions
            if not redaction.is_initial
            and redaction.redaction_date >= document.monitor_from
            and redaction.redaction_id not in known_ids
        ]
        pending.sort(key=self._redaction_order)
        return pending

    @staticmethod
    def _redaction_order(redaction: EbpiRedaction) -> tuple[date, int]:
        """Дата и порядковый номер портала задают последовательность редакций.

        У нескольких редакций может совпадать дата. Числовой redid не задаёт
        хронологию: будущую редакцию портал может подготовить заранее.
        """

        position = re.match(r"^\s*(\d+)\.", redaction.caption or "")
        return redaction.redaction_date, int(position.group(1)) if position else 0

    async def _create_changes_for_redaction(
        self,
        document: TrackedDocument,
        redaction: EbpiRedaction,
        progress: MonitoringDocumentResult,
        previous_redaction: EbpiRedaction | None = None,
    ) -> int:
        """Создаёт события изменений по одной редакции документа."""

        citation = self._parse_amending_citation(caption=redaction.caption)
        if citation is None:
            await self._record("missing_citation", "В подписи редакции нет реквизитов акта-поправки; события не созданы.", {
                "redaction_id": redaction.redaction_id, "caption": redaction.caption,
            }, "warning")
            logger.warning(
                "⚠️ В подписи редакции нет реквизитов акта-поправки. document_id=%s redaction_id=%s",
                document.document_id,
                redaction.redaction_id,
            )
            return 0

        parsed_redaction = await self._build_redaction_document(redaction_id=redaction.redaction_id)
        amending_hash = parsed_redaction.find_amending_document_hash(citation=citation)
        if amending_hash is None:
            await self._record("amending_act_not_found", "Акт-поправка не найден в тексте редакции; события не созданы.", {
                "redaction_id": redaction.redaction_id, "citation": citation,
            }, "warning")
            logger.warning(
                "⚠️ Акт-поправка не найден в тексте редакции. document_id=%s redaction_id=%s акт=%s",
                document.document_id,
                redaction.redaction_id,
                citation,
            )
            return 0

        previous_document = (
            await self._build_redaction_document(redaction_id=previous_redaction.redaction_id)
            if previous_redaction is not None else None
        )
        await self._record("sections_comparison", "Сравнение статей с предыдущей редакцией.", {
            "redaction_id": redaction.redaction_id, "previous_redaction_id": previous_redaction.redaction_id if previous_redaction else None,
            "citation": citation, "amending_hash": amending_hash,
        })
        changed_sections = parsed_redaction.find_sections_changed_by(
            amending_document_hash=amending_hash,
            previous_redaction=previous_document,
        )
        if not changed_sections:
            await self._record("no_changed_sections", "Новых изменений текста статей не найдено.", {"redaction_id": redaction.redaction_id})
            logger.info(
                "ℹ️ Новых изменений текста статей не найдено. document_id=%s redaction_id=%s акт=%s",
                document.document_id,
                redaction.redaction_id,
                citation,
            )
            return 0

        # Вид акта-поправки берётся из его карточки: подпись редакции содержит
        # только номер и дату, а выводить вид из номера означало бы угадывать.
        amending_act = await self._get_amending_act(document_hash=amending_hash)

        changes = []
        for section in changed_sections:
            change = LegalChangeCreateRequest(
                monitoring_check_id=self.recorder.check_id if self.recorder else None,
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

        await self._record("changes_saving", "Сохранение событий изменений.", {
            "redaction_id": redaction.redaction_id, "sections": [item.section_number for item in changes],
        })
        created = await self.legal_changes_repository.save_redaction_changes(data=changes)
        progress.changes_created += created
        await self._record("changes_saved", "События изменений сохранены.", {
            "redaction_id": redaction.redaction_id, "sections": [item.section_number for item in changes],
            "created": created, "already_existed": len(changes) - created,
        })

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
            await self._record("amending_card_unavailable", "Карточка акта-поправки недоступна. Создание событий продолжено без её реквизитов.", {
                "document_hash": document_hash, "error": str(error),
            }, "warning")
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

        await self._record("redaction_loading", "Загрузка текста и оглавления редакции.", {"redaction_id": redaction_id})
        redaction_html = await self.pravo_ebpi_client.get_redaction_text(redaction_id=redaction_id)
        content_nodes = await self.pravo_ebpi_client.get_redaction_content(redaction_id=redaction_id)
        loop = asyncio.get_running_loop()
        parsed = await loop.run_in_executor(
            None,
            functools.partial(
                RedactionDocument,
                redaction_html=redaction_html,
                content_nodes=content_nodes,
            ),
        )

        await self._record("redaction_parsed", "Текст и оглавление редакции разобраны.", {
            "redaction_id": redaction_id, "html_characters": len(redaction_html), "content_nodes": len(content_nodes),
        })
        return parsed

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
