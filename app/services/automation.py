import asyncio
import logging
from datetime import UTC, datetime

from app.clients.verification import VerificationInvalid, VerificationUnavailable
from app.exceptions.pravo_ebpi import PravoEbpiClientError
from app.exceptions.redaction import (
    RedactionNotReadyError,
    RedactionParseError,
    RedactionSectionNotFoundError,
)
from app.services.change_review import official_document_url, redaction_order
from app.services.section_text import SectionTextService
from app.services.verification import (
    compare_all_articles,
    json_digest,
    normalized,
    official_plain_text,
    payload_identity,
    validate_report,
)

logger = logging.getLogger(__name__)


class AutomationService:
    def __init__(self, repository, pravo, verifier, configuration_provider):
        self.repository, self.pravo, self.verifier = repository, pravo, verifier
        self.configuration_provider = configuration_provider

    async def run(self):
        configuration = await self.configuration_provider()
        if configuration.automation_mode == "manual":
            return {"disabled": True, "processed": 0}
        async with self.repository.lock() as acquired:
            if not acquired:
                return {"already_running": True, "processed": 0}
            await self.repository.recover()
            await self.repository.bootstrap()
            processed = 0
            for case_id in await self.repository.pending_ids(include_shadow=configuration.automation_mode == "auto"):
                configuration = await self.configuration_provider()
                if configuration.automation_mode == "manual":
                    break
                run_id = await self.repository.start(case_id, configuration)
                if run_id is None:
                    continue
                await self._review(case_id, run_id, configuration)
                processed += 1
            return {"processed": processed}

    async def _review(self, case_id, run_id, configuration):
        guards, result = {}, {"problems": []}
        try:
            case, document, changes = await self.repository.inputs(case_id)
            guards = {str(c.id): {"updated_at": c.updated_at.isoformat(), "payload": json_digest(payload_identity(c))} for c in changes}
            snapshot = await self._snapshot(case, document, changes)
            await self.repository.progress(run_id, "comparison", "Редакции сопоставлены. Проверяется полнота статей.", snapshot=snapshot)
            await self.repository.progress(run_id, "llm", "LLM сверяет закон, даты и все изменённые статьи.")
            report, metadata = await self.verifier.review(snapshot, configuration.verification_model)
            problems = validate_report(snapshot, report)
            result = {"report": report.model_dump(mode="json"), "llm": metadata, "problems": problems,
                      "checks": {"full_revision_diff": True, "law_coverage_and_evidence": not problems}}
            await self.repository.progress(run_id, "decision", "Фиксируется результат проверки.", result=result)
        except (VerificationUnavailable, PravoEbpiClientError, RedactionNotReadyError) as error:
            return await self.repository.finish(run_id, result, guards, transient_error=str(error)[:3000])
        except (VerificationInvalid, RedactionParseError, RedactionSectionNotFoundError) as error:
            result["problems"] = [str(error)[:3000]]
        except Exception:
            logger.exception("Непредвиденная ошибка автоматической проверки case_id=%s run_id=%s", case_id, run_id)
            result["problems"] = ["Внутренняя ошибка проверки. Решение не принято; подробности в серверном журнале."]
        return await self.repository.finish(run_id, result, guards)

    async def _snapshot(self, case, document, changes):
        if not document.is_active:
            raise VerificationInvalid("Мониторинг документа приостановлен.")
        if not document.ebpi_doc_hash or not case.amending_doc_hash:
            raise VerificationInvalid("Не определён официальный документ или закон-поправка.")
        document_card = await self.pravo.get_document_card_by_hash(document.ebpi_doc_hash)
        if (document_card.doc_hash != document.ebpi_doc_hash or not document_card.adoption
                or document_card.adoption.act_number != document.document_number
                or document_card.adoption.act_date != document.adoption_date):
            raise VerificationInvalid("Официальный основной документ не совпадает с реквизитами отслеживаемого документа.")
        if not changes:
            raise VerificationInvalid("Редакция обнаружена, но события статей не созданы. Требуется проверить полноту мониторинга.")
        redactions = sorted(await self.pravo.get_redactions(document.ebpi_doc_hash), key=redaction_order)
        position = next((i for i, item in enumerate(redactions) if item.redaction_id == case.redaction_id), None)
        if position is None or position == 0:
            raise VerificationInvalid("Нет редакции события или её непосредственной предшественницы.")
        current, previous = redactions[position], redactions[position - 1]
        if not current.is_completed or not previous.is_completed:
            raise RedactionNotReadyError("Официальный портал ещё готовит тексты редакций.")
        if current.redaction_date == previous.redaction_date:
            raise VerificationInvalid("Несколько редакций с одной датой: требуется проверить последовательность поправок.")
        if any(c.amending_doc_hash != case.amending_doc_hash or c.redaction_date != current.redaction_date or c.effective_date != current.redaction_date for c in changes):
            raise VerificationInvalid("Источник или даты событий не совпадают с проверяемой редакцией.")
        law_card = await self.pravo.get_document_card_by_hash(case.amending_doc_hash)
        if law_card.doc_hash != case.amending_doc_hash:
            raise VerificationInvalid("Портал вернул другой закон-поправку.")
        adoption = law_card.adoption
        if not adoption or any((c.amending_act_number, c.amending_act_date, c.amending_act_type) != (adoption.act_number, adoption.act_date, adoption.act_type) for c in changes):
            raise VerificationInvalid("Реквизиты закона в событиях расходятся с официальной карточкой.")
        law_redactions = await self.pravo.get_redactions(case.amending_doc_hash)
        # Если сам закон уже исправлен, оригинал не даёт достаточного основания для автопроверки.
        if len(law_redactions) != 1 or not law_redactions[0].is_initial:
            raise VerificationInvalid("Закон-поправка имеет несколько редакций. Требуется разбор последующих изменений.")
        if not law_redactions[0].is_completed:
            raise RedactionNotReadyError("Текст закона-поправки ещё не готов.")
        law_html = await self.pravo.get_redaction_text(law_redactions[0].redaction_id)
        text_service = SectionTextService(self.pravo)
        parsed_previous = await text_service._build_document(previous.redaction_id)
        parsed_current = await text_service._build_document(current.redaction_id)
        articles = await asyncio.to_thread(compare_all_articles, parsed_previous, parsed_current)
        if not articles:
            raise VerificationInvalid("Полный diff редакций не содержит изменений статей.")
        for change in changes:
            citation = f"от {adoption.act_date:%d.%m.%Y} № {adoption.act_number}"
            if normalized(change.amending_law_ref) != normalized(citation):
                raise VerificationInvalid("Реквизиты события не совпадают с законом-поправкой.")
        return {
            "target_document": {"document_id": document.document_id, "title": document.full_title,
                                "number": document.document_number, "adoption_date": str(document.adoption_date),
                                "url": official_document_url(document.ebpi_doc_hash)},
            "law": {"title": law_card.doc_name, "reference": law_card.doc_passing,
                    "hash": case.amending_doc_hash, "redaction_id": law_redactions[0].redaction_id,
                    "url": official_document_url(case.amending_doc_hash), "text": official_plain_text(law_html)},
            "revision": {"id": current.redaction_id, "date": str(current.redaction_date),
                         "previous_id": previous.redaction_id, "previous_date": str(previous.redaction_date)},
            "articles": articles,
            "events": [{"id": c.id, "section_number": c.section_number, "effective_date": str(c.effective_date)} for c in changes],
            "captured_at": datetime.now(UTC).isoformat(),
        }
