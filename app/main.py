import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.admin import create_admin
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.legal_changes import router as legal_changes_router
from app.api.v1.endpoints.tracked_documents import router as tracked_documents_router
from app.background_tasks.scheduler import create_scheduler
from app.core.config_logger import configure_logging
from app.core.settings import get_settings
from app.db.session import engine
from app.utils.check_db import check_db_connection

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Управляет жизненным циклом приложения."""

    logger.info("🚀 Старт Legal Sync Service.")
    await check_db_connection()
    logger.info("✅ PostgreSQL доступен.")
    global scheduler
    if settings.scheduler.scheduler_enabled:
        scheduler = create_scheduler(settings=settings.scheduler)
        scheduler.start()
        logger.info("✅ Планировщик запущен.")
    yield
    logger.info("🔄 Остановка Legal Sync Service.")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    await engine.dispose()


app = FastAPI(
    title=settings.app.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

create_admin(app=app, engine=engine)

app.include_router(health_router, prefix=settings.app.api_v1_prefix)
app.include_router(tracked_documents_router, prefix=settings.app.api_v1_prefix)
app.include_router(legal_changes_router, prefix=settings.app.api_v1_prefix)
