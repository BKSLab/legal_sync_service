from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import Settings, get_settings
from app.db.session import async_session_factory
from app.exceptions.configuration import ConfigurationUnavailableError
from app.repositories.configuration import ConfigurationRepository
from app.schemas.configuration import ConfigurationSnapshot, ConfigurationValues


def initial_configuration(settings: Settings) -> ConfigurationValues:
    """Окружение используется только при первом заполнении таблицы."""
    return ConfigurationValues(
        rag_delivery_enabled=settings.rag.rag_delivery_enabled,
        monitoring_cron_hour=settings.scheduler.monitoring_cron_hour,
        processing_cron_hour=str(settings.scheduler.processing_cron_hour),
        timezone=settings.scheduler.timezone,
        processing_max_retries=settings.scheduler.processing_max_retries,
    )


async def load_configuration() -> ConfigurationSnapshot:
    """Свежая конфигурация из БД, без кэша конкретного воркера."""
    try:
        async with async_session_factory() as session:
            return await ConfigurationRepository(session).get_or_create(initial_configuration(get_settings()))
    except (SQLAlchemyError, OSError, ValidationError) as error:
        raise ConfigurationUnavailableError(ConfigurationUnavailableError.detail) from error
