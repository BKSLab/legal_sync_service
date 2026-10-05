from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from app.clients.verification import POLICY_VERSION
from app.db.models import (
    AutomationCase,
    AutomationRun,
    LegalChange,
    LegalChangeStatus,
    ServiceConfiguration,
    TrackedDocument,
)
from app.services.verification import digest, json_digest, payload_identity

AUTOMATION_LOCK_KEY = 8_401_003


class AutomationRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    @asynccontextmanager
    async def lock(self):
        async with self.sessions() as session, session.begin():
            acquired = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": AUTOMATION_LOCK_KEY})
            yield bool(acquired)

    async def ensure_case(self, document_id: int, redaction_id: int, amending_hash: str | None):
        async with self.sessions() as session, session.begin():
            statement = insert(AutomationCase).values(
                tracked_document_id=document_id, redaction_id=redaction_id, amending_doc_hash=amending_hash,
                status="pending", stage="queued", attempts=0,
            )
            await session.execute(statement.on_conflict_do_update(
                constraint="uq_automation_document_redaction",
                set_={"amending_doc_hash": statement.excluded.amending_doc_hash},
                where=AutomationCase.amending_doc_hash.is_(None) & statement.excluded.amending_doc_hash.is_not(None),
            ))

    async def bootstrap(self):
        async with self.sessions() as session:
            groups = (await session.execute(select(
                LegalChange.tracked_document_id, LegalChange.ebpi_redaction_id, func.max(LegalChange.amending_doc_hash),
            ).where(LegalChange.status == LegalChangeStatus.DRAFT, LegalChange.ebpi_redaction_id.is_not(None))
                .group_by(LegalChange.tracked_document_id, LegalChange.ebpi_redaction_id))).all()
        for document_id, redaction_id, law_hash in groups:
            await self.ensure_case(document_id, redaction_id, law_hash)

    async def recover(self):
        async with self.sessions() as session, session.begin():
            now = datetime.now(UTC)
            await session.execute(update(AutomationRun).where(AutomationRun.status == "running").values(
                status="interrupted", finished_at=now, error="Процесс проверки прерван. Решение не принято.",
            ))
            cases = (await session.scalars(select(AutomationCase).where(AutomationCase.status == "running"))).all()
            for case in cases:
                case.status = "error" if case.attempts < 3 else "needs_review"
                case.message = "Проверка прервана; будет повторена." if case.attempts < 3 else "Исчерпаны попытки после прерываний."
                case.next_attempt_at = now + timedelta(minutes=5)

    async def pending_ids(self, limit=10, include_shadow=False):
        async with self.sessions() as session:
            return list(await session.scalars(select(AutomationCase.id).where(
                AutomationCase.status.in_(["pending", "error", *(["shadow_pass"] if include_shadow else [])]),
                or_(AutomationCase.next_attempt_at.is_(None), AutomationCase.next_attempt_at <= datetime.now(UTC)),
            ).order_by(AutomationCase.id).limit(limit)))

    async def start(self, case_id, configuration):
        async with self.sessions() as session, session.begin():
            case = await session.get(AutomationCase, case_id, with_for_update=True)
            if case.status not in ("pending", "error", "shadow_pass"):
                return None
            run = AutomationRun(case_id=case.id, status="running", stage="sources", mode=configuration.automation_mode,
                                model=configuration.verification_model, policy_version=POLICY_VERSION,
                                configuration_version=configuration.version, snapshot={}, result={})
            session.add(run)
            await session.flush()
            case.status, case.stage, case.latest_run_id = "running", "sources", run.id
            case.attempts += 1
            case.message = "Загружаются официальные источники."
            return run.id

    async def inputs(self, case_id):
        async with self.sessions() as session:
            case = await session.get(AutomationCase, case_id)
            document = await session.get(TrackedDocument, case.tracked_document_id)
            changes = list(await session.scalars(select(LegalChange).options(joinedload(LegalChange.tracked_document)).where(
                LegalChange.tracked_document_id == case.tracked_document_id,
                LegalChange.ebpi_redaction_id == case.redaction_id,
            ).order_by(LegalChange.id)))
            return case, document, changes

    async def progress(self, run_id, stage, message, snapshot=None, result=None):
        async with self.sessions() as session, session.begin():
            run = await session.get(AutomationRun, run_id)
            case = await session.get(AutomationCase, run.case_id)
            run.stage = case.stage = stage
            case.message = message
            if snapshot is not None:
                run.snapshot, run.input_sha256 = snapshot, json_digest(snapshot)
            if result is not None:
                run.result = result

    async def finish(self, run_id, result, guards, *, transient_error=None):
        """Проверка версий и решение по всему набору в одной транзакции."""
        async with self.sessions() as session, session.begin():
            run = await session.get(AutomationRun, run_id, with_for_update=True)
            case = await session.get(AutomationCase, run.case_id, with_for_update=True)
            now = datetime.now(UTC)
            run.finished_at, run.result = now, result
            problems = list(result.get("problems", []))
            if transient_error:
                run.status, run.error = "error", transient_error
                case.status = "error" if case.attempts < 3 else "needs_review"
                case.next_attempt_at = now + timedelta(minutes=5 * case.attempts)
                case.message = transient_error + (" Повтор запланирован." if case.status == "error" else " Исчерпаны три попытки.")
                return case.status
            configuration = await session.get(ServiceConfiguration, 1, with_for_update=True)
            await session.get(TrackedDocument, case.tracked_document_id, with_for_update=True)
            changes = list(await session.scalars(select(LegalChange).options(joinedload(LegalChange.tracked_document)).where(
                LegalChange.tracked_document_id == case.tracked_document_id, LegalChange.ebpi_redaction_id == case.redaction_id,
            ).with_for_update(of=LegalChange)))
            actual_guards = {str(c.id): {"updated_at": c.updated_at.isoformat(), "payload": json_digest(payload_identity(c))} for c in changes}
            if configuration is None or configuration.version != run.configuration_version:
                problems.append("Конфигурация изменена во время проверки. Решение не применено.")
            texts = {a["section_number"]: a["after"] for a in run.snapshot.get("articles", [])}
            if actual_guards != guards:
                problems.append("Состав или состояние событий изменились; повторите проверку актуальных данных.")
            for change in changes:
                if change.status == LegalChangeStatus.DRAFT:
                    continue
                if change.review_origin == "auto" and change.status == LegalChangeStatus.FAILED:
                    continue
                if change.review_origin == "auto" and change.status in (LegalChangeStatus.SCHEDULED, LegalChangeStatus.SENT):
                    if (change.verified_text_sha256 == digest(texts.get(change.section_number, ""))
                            and change.verified_payload_sha256 == json_digest(payload_identity(change))):
                        continue
                problems.append(f"Событие №{change.id}: существующее решение не допускает автоподтверждение. Нужен оператор.")
            if problems:
                run.status = case.status = "needs_review"
                run.result = {**result, "problems": list(dict.fromkeys(problems))}
                case.message = " ".join(run.result["problems"])[:4000]
            elif run.mode == "shadow":
                run.status = case.status = "shadow_pass"
                case.message = "Все проверки пройдены. Режим наблюдения: отправка не разрешена."
            elif run.mode == "auto" and configuration.automation_mode == "auto":
                scheduled = 0
                for change in changes:
                    # Повтор после частичной доставки не меняет уже отправленные
                    # или ожидающие срока статьи. Их прежние доказательства сохранены.
                    if change.status in (LegalChangeStatus.SCHEDULED, LegalChangeStatus.SENT):
                        continue
                    change.status = LegalChangeStatus.SCHEDULED
                    change.send_at = datetime.combine(change.effective_date, time.min, tzinfo=UTC)
                    change.review_origin, change.reviewed_by, change.reviewed_at = "auto", "Автоматическая проверка", now
                    change.automation_run_id = run.id
                    change.review_notes = result["report"]["summary"]
                    change.consolidated_text = texts[change.section_number]
                    change.consolidated_text_source = f"actual.pravo.gov.ru: редакция {change.ebpi_redaction_id} от {change.redaction_date}"
                    change.verified_text_sha256 = digest(change.consolidated_text)
                    change.verified_payload_sha256 = json_digest(payload_identity(change))
                    change.last_error = None
                    change.retry_count = 0
                    scheduled += 1
                run.status = case.status = "approved"
                case.message = f"Проверки пройдены. Запланировано статей: {scheduled}. Отправка после вступления в силу."
            else:
                run.status = case.status = "needs_review"
                case.message = "Автоподтверждение выключено. Решение не применено."
            run.stage = case.stage = "decision"
            return case.status

    async def retry(self, case_id):
        async with self.sessions() as session, session.begin():
            case = await session.get(AutomationCase, case_id, with_for_update=True)
            if case is None or case.status not in ("needs_review", "error", "shadow_pass", "interrupted"):
                return False
            case.status, case.stage, case.attempts, case.next_attempt_at = "pending", "queued", 0, None
            case.message = "Повторная проверка запрошена оператором."
            return True

    async def delivery_exception(self, change, message):
        async with self.sessions() as session, session.begin():
            await session.execute(update(AutomationCase).where(
                AutomationCase.tracked_document_id == change.tracked_document_id,
                AutomationCase.redaction_id == change.ebpi_redaction_id,
            ).values(status="needs_review", stage="delivery", message=message))

    async def refresh_outcome(self, change):
        """Сводный статус пакета после доставки или ручного разбора исключения."""
        async with self.sessions() as session, session.begin():
            case = await session.scalar(select(AutomationCase).where(
                AutomationCase.tracked_document_id == change.tracked_document_id,
                AutomationCase.redaction_id == change.ebpi_redaction_id,
            ).with_for_update())
            if case is None or case.status == "running":
                return
            changes = list(await session.scalars(select(LegalChange).where(
                LegalChange.tracked_document_id == case.tracked_document_id,
                LegalChange.ebpi_redaction_id == case.redaction_id,
            )))
            if changes and all(c.status == LegalChangeStatus.SENT for c in changes):
                case.status, case.stage = "delivered", "delivery"
                case.message = f"Все статьи ({len(changes)}) приняты RAG с проверенной контрольной суммой."
            elif changes and all(c.status in (LegalChangeStatus.SCHEDULED, LegalChangeStatus.SENT, LegalChangeStatus.CANCELLED) for c in changes) and any(c.review_origin == "human" for c in changes):
                case.status, case.stage = "resolved", "decision"
                case.message = "Исключение разобрано оператором. Решения по статьям и история автопроверок сохранены."
