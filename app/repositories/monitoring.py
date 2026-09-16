import asyncio
import logging
import os
import socket
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.configuration import ServiceConfiguration
from app.db.models.monitoring import MonitoringDocumentCheck, MonitoringLogEntry, MonitoringRun
from app.exceptions.monitoring import MonitoringJournalError

logger = logging.getLogger(__name__)
MONITORING_LOCK_KEY = 8_401_001


def error_details(error: BaseException) -> dict:
    return {
        "error": f"{type(error).__name__}: {error}",
        # Локальные переменные, настройки и заголовки запросов в журнал не попадают.
        "error_traceback": "".join(traceback.format_exception(error)),
    }


class MonitoringJournal:
    """Отдельные короткие транзакции переживают откат рабочей сессии.

    Общая блокировка объединяет API и все воркеры планировщика. Только её
    владелец может объявить старые running прерванными; возраст записи для
    этого не используется, поэтому медленный живой запуск не теряется.
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory

    @asynccontextmanager
    async def transaction(self):
        try:
            async with self.session_factory() as session, session.begin():
                yield session
        except (SQLAlchemyError, OSError) as error:
            logger.exception("Не удалось записать журнал мониторинга.")
            raise MonitoringJournalError() from error

    @asynccontextmanager
    async def lock(self):
        async with self.transaction() as session:
            acquired = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": MONITORING_LOCK_KEY})
            yield bool(acquired)

    async def recover_abandoned(self) -> None:
        async with self.lock() as acquired:
            if acquired:
                await self._recover()

    async def _recover(self) -> None:
        reason = "Процесс завершился без итогового результата. Обрыв обнаружен после освобождения блокировки мониторинга."
        async with self.transaction() as session:
            runs = (await session.scalars(select(MonitoringRun).where(MonitoringRun.status == "running"))).all()
            for run in runs:
                run.status, run.error = "interrupted", reason
                # Точный момент аварии неизвестен. finished_at оставляем пустым.
                await session.execute(update(MonitoringDocumentCheck).where(
                    MonitoringDocumentCheck.run_id == run.id, MonitoringDocumentCheck.status == "running",
                ).values(status="interrupted", error=reason))
                session.add(MonitoringLogEntry(
                    run_id=run.id, created_at=datetime.now(UTC), level="error",
                    stage="interrupted", message=reason,
                ))
                logger.warning("Запуск мониторинга признан прерванным. run_id=%s", run.id)

    @asynccontextmanager
    async def start(self, source: str):
        scheduled_for = datetime.now(UTC).replace(second=0, microsecond=0) if source == "scheduled" else None
        async with self.lock() as acquired:
            reason = None
            if acquired:
                await self._recover()
                # Быстрый запуск может завершиться до пробуждения второго воркера.
                # Для одной минуты расписания повторный обход не нужен.
                if scheduled_for is not None:
                    async with self.transaction() as session:
                        previous = await session.scalar(select(MonitoringRun.id).where(MonitoringRun.scheduled_for == scheduled_for))
                    if previous is not None:
                        reason = f"Проверка по этому времени расписания уже зарегистрирована: запуск №{previous}."
            else:
                reason = "Мониторинг уже выполняется другим запуском. Повторный обход пропущен."
            now = datetime.now(UTC)
            async with self.transaction() as session:
                configuration = await session.get(ServiceConfiguration, 1)
                snapshot = {
                    name: getattr(configuration, name) for name in (
                        "version", "monitoring_enabled", "monitoring_cron_hour", "timezone", "rag_delivery_enabled",
                    )
                } if configuration else None
                run = MonitoringRun(
                    source=source, status="skipped" if reason else "running",
                    started_at=now, updated_at=now, finished_at=now if reason else None,
                    scheduled_for=None if reason else scheduled_for,
                    worker=f"{socket.gethostname()}:{os.getpid()}", configuration=snapshot, error=reason,
                )
                session.add(run)
                await session.flush()
                run_id = run.id
                session.add(MonitoringLogEntry(
                    run_id=run_id, created_at=now, level="info", stage="skipped" if reason else "started",
                    message=reason or "Мониторинг запущен.",
                ))
            recorder = MonitoringRunRecorder(self, run_id, skipped=bool(reason))
            try:
                yield recorder
            except asyncio.CancelledError as error:
                await recorder.fail(error, interrupted=True)
                raise
            except Exception as error:
                logger.exception("Запуск мониторинга завершился ошибкой. run_id=%s", run_id)
                try:
                    await recorder.fail(error)
                except MonitoringJournalError:
                    logger.exception("Итог запуска не сохранён. run_id=%s", run_id)
                raise


class MonitoringRunRecorder:
    def __init__(self, journal: MonitoringJournal, run_id: int, skipped: bool = False):
        self.journal, self.run_id, self.skipped = journal, run_id, skipped
        self.check_id = None
        self.progress = None

    async def _progress(self, session) -> None:
        if self.check_id is not None and self.progress is not None:
            await session.execute(update(MonitoringDocumentCheck).where(MonitoringDocumentCheck.id == self.check_id).values(
                **self.progress.model_dump(include={
                    "redactions_total", "redactions_pending", "redactions_skipped_incomplete", "changes_created",
                }),
            ))
        counts = (await session.execute(select(
            func.count().filter(MonitoringDocumentCheck.status.in_(["succeeded", "warnings", "failed"])),
            func.count().filter(MonitoringDocumentCheck.status == "failed"),
            func.coalesce(func.sum(MonitoringDocumentCheck.changes_created), 0),
        ).where(MonitoringDocumentCheck.run_id == self.run_id))).one()
        await session.execute(update(MonitoringRun).where(MonitoringRun.id == self.run_id).values(
            updated_at=datetime.now(UTC), documents_checked=counts[0], documents_failed=counts[1], changes_created=counts[2],
        ))

    async def set_total(self, count: int) -> None:
        async with self.journal.transaction() as session:
            await session.execute(update(MonitoringRun).where(MonitoringRun.id == self.run_id).values(documents_total=count))
        await self.event("documents_selected", f"Документов на контроле: {count}.")

    async def begin_document(self, document, progress) -> None:
        self.progress = progress
        async with self.journal.transaction() as session:
            check = MonitoringDocumentCheck(
                run_id=self.run_id, tracked_document_id=document.id, document_id=document.document_id,
                document_title=document.short_name, status="running", started_at=datetime.now(UTC),
                parameters={
                    "monitor_from": document.monitor_from.isoformat(), "document_number": document.document_number,
                    "adoption_date": document.adoption_date.isoformat(), "ebpi_doc_hash": document.ebpi_doc_hash,
                },
            )
            session.add(check)
            await session.flush()
            self.check_id = check.id
        await self.event("document_started", "Проверка документа начата.")

    async def event(self, stage: str, message: str, details: dict | None = None, level: str = "info") -> None:
        async with self.journal.transaction() as session:
            session.add(MonitoringLogEntry(
                run_id=self.run_id, check_id=self.check_id, created_at=datetime.now(UTC),
                level=level, stage=stage, message=message, details=details,
            ))
            if level == "warning":
                await session.execute(update(MonitoringRun).where(MonitoringRun.id == self.run_id).values(
                    warnings_count=MonitoringRun.warnings_count + 1,
                ))
                if self.check_id is not None:
                    await session.execute(update(MonitoringDocumentCheck).where(MonitoringDocumentCheck.id == self.check_id).values(
                        warnings_count=MonitoringDocumentCheck.warnings_count + 1,
                    ))
            await self._progress(session)

    async def finish_document(self, error: Exception | None = None) -> None:
        async with self.journal.transaction() as session:
            check = await session.get(MonitoringDocumentCheck, self.check_id)
            check.status = "failed" if error else "warnings" if check.warnings_count else "succeeded"
            check.finished_at = datetime.now(UTC)
            if error:
                for key, value in error_details(error).items():
                    setattr(check, key, value)
            await session.flush()
            await self._progress(session)
        await self.event(
            "document_finished", "Проверка документа завершилась ошибкой." if error else "Проверка документа завершена.",
            self.progress.model_dump(), "error" if error else "info",
        )
        self.check_id, self.progress = None, None

    async def finish(self, result) -> None:
        await self.event("finished", (
            f"Мониторинг завершён. Проверено документов: {result.documents_checked}; "
            f"новых событий: {result.changes_created}; ошибок документов: {result.documents_failed}."
        ), result.model_dump(exclude={"items"}))
        async with self.journal.transaction() as session:
            run = await session.get(MonitoringRun, self.run_id)
            run.status = "failed" if result.documents_failed else "warnings" if run.warnings_count else "succeeded"
            run.finished_at = datetime.now(UTC)

    async def fail(self, error: BaseException, interrupted: bool = False) -> None:
        details = error_details(error)
        if interrupted:
            details["error"] = "Запуск отменён при остановке процесса."
        async with self.journal.transaction() as session:
            now = datetime.now(UTC)
            if self.check_id is not None:
                await session.execute(update(MonitoringDocumentCheck).where(MonitoringDocumentCheck.id == self.check_id).values(
                    status="interrupted" if interrupted else "failed", finished_at=now, **details,
                ))
            await self._progress(session)
            await session.execute(update(MonitoringRun).where(MonitoringRun.id == self.run_id).values(
                status="interrupted" if interrupted else "failed", finished_at=now, **details,
            ))
            session.add(MonitoringLogEntry(
                run_id=self.run_id, check_id=self.check_id, created_at=now, level="error",
                stage="interrupted" if interrupted else "failed", message=details["error"],
            ))
