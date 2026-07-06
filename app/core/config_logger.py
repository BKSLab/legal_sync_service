import logging
import logging.config
from pathlib import Path

from app.core.settings import get_settings


def configure_logging() -> None:
    """Инициализирует логирование из logging.ini."""

    settings = get_settings()
    config_path = Path(settings.app.logging_config_path)
    if config_path.exists():
        logging.config.fileConfig(config_path, disable_existing_loggers=False)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s %(funcName)s %(message)s",
        )


logger = logging.getLogger("legal_sync_service")
