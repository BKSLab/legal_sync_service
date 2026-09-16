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
from app.core.settings import SchedulerSettings, get_settings
from app.db.session import async_session_factory
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.services.monitoring import MonitoringService
from app.services.processing import ProcessingService

logger = logging.getLogger(__name__)

# Ключи advisory-lock PostgreSQL. Задачи из `lifespan` стартуют в каждом
# воркере ASGI-сервера, поэтому без блокировки один и тот же мониторинг
# запустился бы столько раз, сколько воркеров поднято.
MONITORING_LOCK_KEY = 8_401_001


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

    settings = get_settings()

    async def job() -> None:
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
    if not settings.rag.rag_delivery_enabled:
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
            max_retries=settings.scheduler.processing_max_retries,
            delivery_enabled=settings.rag.rag_delivery_enabled,
        )
        await service.run_processing()


def create_scheduler(
    settings: SchedulerSettings, *, rag_delivery_enabled: bool = False,
) -> AsyncIOScheduler:
    """Создает APScheduler с задачами Legal Sync Service."""

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        run_monitoring_job,
        CronTrigger(hour=settings.monitoring_cron_hour, minute=0, timezone=settings.timezone),
        id="legal_sync_monitoring",
        replace_existing=True,
    )
    logger.info(
        "Расписание мониторинга: часы=%s, минута=00, часовой пояс=%s.",
        settings.monitoring_cron_hour, settings.timezone,
    )
    if rag_delivery_enabled:
        scheduler.add_job(
            run_processing_job,
            CronTrigger(hour=settings.processing_cron_hour, minute=0, timezone=settings.timezone),
            id="legal_sync_processing",
            replace_existing=True,
        )
    else:
        logger.info("Отправка в RAG отключена; работает только мониторинг изменений.")
    return scheduler
