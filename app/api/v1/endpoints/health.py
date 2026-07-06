import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.dependencies.db_session import DbSessionDep
from app.schemas.health import HealthSchema

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get(
    path="/health",
    response_model=HealthSchema,
    summary="Проверка состояния сервиса",
    description="Проверяет доступность приложения и подключение к PostgreSQL.",
    operation_id="getHealth",
    response_description="Текущее состояние сервиса.",
)
async def get_health(session: DbSessionDep) -> HealthSchema:
    """Проверяет состояние приложения и базы данных."""

    logger.info("🚀 Запрос GET /health.")
    await session.execute(text("SELECT 1"))
    logger.info("✅ Запрос GET /health выполнен.")
    return HealthSchema(status="ok", database="ok")
