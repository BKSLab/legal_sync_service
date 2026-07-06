import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.settings import SchedulerSettings

logger = logging.getLogger(__name__)


async def run_monitoring_job() -> None:
    """Запускает мониторинг публикаций.

    Реальная бизнес-логика будет подключена после реализации анализа законов-поправок.
    """

    logger.info("🚀 Плановая задача мониторинга запущена.")
    logger.info("✅ Плановая задача мониторинга завершена: обработчик пока не подключен.")


async def run_processing_job() -> None:
    """Запускает обработку событий, готовых к отправке в RAG."""

    logger.info("🚀 Плановая задача обработки очереди запущена.")
    logger.info("✅ Плановая задача обработки очереди завершена: обработчик пока не подключен.")


def create_scheduler(settings: SchedulerSettings) -> AsyncIOScheduler:
    """Создает APScheduler с задачами Legal Sync Service."""

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        run_monitoring_job,
        CronTrigger(hour=settings.monitoring_cron_hour, minute=0),
        id="legal_sync_monitoring",
        replace_existing=True,
    )
    scheduler.add_job(
        run_processing_job,
        CronTrigger(hour=settings.processing_cron_hour, minute=0),
        id="legal_sync_processing",
        replace_existing=True,
    )
    return scheduler
