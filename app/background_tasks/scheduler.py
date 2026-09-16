import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pravo_ebpi import PravoEbpiClient
from app.clients.rag import RagClient
from app.core.settings import get_settings
from app.db.session import async_session_factory
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.configuration import ConfigurationValues
from app.services.configuration import load_configuration
from app.services.monitoring import MonitoringService
from app.services.processing import ProcessingService

logger = logging.getLogger(__name__)

# Ключи advisory-lock PostgreSQL. Задачи из `lifespan` стартуют в каждом
# воркере ASGI-сервера, поэтому без блокировки один и тот же мониторинг
# запустился бы столько раз, сколько воркеров поднято.
MONITORING_LOCK_KEY = 8_401_001
CONFIGURATION_REFRESH_SECONDS = 15


@asynccontextmanager
async def _job_context() -> AsyncGenerator[tuple[AsyncSession, httpx.AsyncClient], None]:
    """Готовит короткоживущие сессию БД и HTTP-клиент на один запуск задачи."""

    async with async_session_factory() as db_session:
        async with httpx.AsyncClient() as httpx_client:
            yield db_session, httpx_client


async def _run_with_lock(lock_key: int, job_name: str, job: Callable) -> None:
    """Выполняет задачу под advisory-lock, если её не выполняет другой воркер."""

    async with async_session_factory() as lock_session:
        acquired = await lock_session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": lock_key},
        )
        if not acquired.scalar():
            logger.info("⚠️ Задача %s уже выполняется другим воркером, пропуск.", job_name)
            return
        try:
            await job()
        finally:
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": lock_key},
            )


async def run_monitoring_job() -> None:
    """Плановая задача: ищет новые редакции документов на контроле."""

    if not (await load_configuration()).monitoring_enabled:
        logger.info("Автоматический мониторинг приостановлен в конфигурации.")
        return
    settings = get_settings()

    async def job() -> None:
        if not (await load_configuration()).monitoring_enabled:
            return
        async with _job_context() as (db_session, httpx_client):
            service = MonitoringService(
                tracked_documents_repository=TrackedDocumentsRepository(db_session=db_session),
                legal_changes_repository=LegalChangesRepository(db_session=db_session),
                pravo_ebpi_client=PravoEbpiClient(
                    httpx_client=httpx_client,
                    settings=settings.pravo_ebpi,
                ),
            )
            await service.run_monitoring()

    await _run_with_lock(
        lock_key=MONITORING_LOCK_KEY,
        job_name="мониторинга",
        job=job,
    )


async def run_processing_job() -> None:
    """Плановая задача: отправляет в RAG Service события, у которых наступил срок."""

    settings = get_settings()
    if not (await load_configuration()).rag_delivery_enabled:
        logger.info("Отправка в RAG отключена; плановая обработка пропущена.")
        return

    async with _job_context() as (db_session, httpx_client):
        service = ProcessingService(
            legal_changes_repository=LegalChangesRepository(db_session=db_session),
            pravo_ebpi_client=PravoEbpiClient(
                httpx_client=httpx_client,
                settings=settings.pravo_ebpi,
            ),
            rag_client=RagClient(httpx_client=httpx_client, settings=settings.rag),
            configuration_provider=load_configuration,
        )
        await service.run_processing()


def apply_configuration(scheduler: AsyncIOScheduler, configuration: ConfigurationValues) -> None:
    """Меняет только изменившиеся задания, сохраняя ближайшие запуски остальных."""
    for job_id, function, enabled, hour in (
        ("legal_sync_monitoring", run_monitoring_job, configuration.monitoring_enabled, configuration.monitoring_cron_hour),
        ("legal_sync_processing", run_processing_job, configuration.rag_delivery_enabled, configuration.processing_cron_hour),
    ):
        job = scheduler.get_job(job_id)
        if not enabled:
            if job is not None:
                scheduler.remove_job(job_id)
                logger.info("Задача %s отключена в конфигурации.", job_id)
            continue
        trigger = CronTrigger(hour=hour, minute=0, timezone=configuration.timezone)
        if job is None:
            scheduler.add_job(
                function, trigger, id=job_id, replace_existing=True,
                coalesce=True, max_instances=1, misfire_grace_time=60,
            )
            logger.info("Задача %s: часы=%s, минута=00, часовой пояс=%s.", job_id, hour, configuration.timezone)
        elif str(job.trigger) != str(trigger) or job.trigger.timezone != trigger.timezone:
            scheduler.reschedule_job(job_id, trigger=trigger)
            logger.info("Расписание %s обновлено: часы=%s, часовой пояс=%s.", job_id, hour, configuration.timezone)


def create_scheduler(configuration: ConfigurationValues) -> AsyncIOScheduler:
    """Каждый воркер обновляет расписание из общей БД без перезапуска."""
    scheduler = AsyncIOScheduler(timezone=configuration.timezone)
    apply_configuration(scheduler, configuration)

    async def refresh_configuration() -> None:
        apply_configuration(scheduler, await load_configuration())

    scheduler.add_job(
        refresh_configuration, "interval", seconds=CONFIGURATION_REFRESH_SECONDS,
        id="legal_sync_configuration", coalesce=True, max_instances=1,
    )
    return scheduler
