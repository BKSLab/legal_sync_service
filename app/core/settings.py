from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class SettingsBase(BaseSettings):
    """Базовый класс настроек проекта."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSettings(SettingsBase):
    """Общие настройки приложения."""

    app_name: str = "Legal Sync Service"
    api_v1_prefix: str = "/api/v1"
    logging_config_path: str = "logging.ini"


class DBSettings(SettingsBase):
    """Настройки подключения к PostgreSQL."""

    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: SecretStr
    postgres_name: str

    @property
    def url_connect(self) -> str:
        return self._build_url("postgresql+asyncpg")

    @property
    def sync_url_connect(self) -> str:
        return self._build_url("postgresql+psycopg")

    def _build_url(self, driver: str) -> str:
        return URL.create(
            drivername=driver,
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_name,
        ).render_as_string(hide_password=False)


class AdminSettings(SettingsBase):
    """Настройки административного интерфейса."""

    secret_key: SecretStr
    admin_login: SecretStr
    admin_password: SecretStr
    admin_session_https_only: bool = False


class ApiSettings(SettingsBase):
    """Настройки служебного REST API."""

    api_key: SecretStr


class PublicationPravoSettings(SettingsBase):
    """Настройки клиента publication.pravo.gov.ru."""

    FEDERAL_LAW_DOCUMENT_TYPE_ID: str = "82a8bf1c-3bc7-47ed-827f-7affd43a7f27"
    GOVERNMENT_DECREE_DOCUMENT_TYPE_ID: str = "fd5a8766-f6fd-4ac2-8fd9-66f414d314ac"

    publication_pravo_base_url: str = "http://publication.pravo.gov.ru"
    publication_pravo_timeout_seconds: int = 30
    publication_pravo_monitoring_blocks: str = "president,government"
    publication_pravo_federal_law_document_type_id: str = FEDERAL_LAW_DOCUMENT_TYPE_ID
    publication_pravo_government_decree_document_type_id: str = GOVERNMENT_DECREE_DOCUMENT_TYPE_ID

    @property
    def monitoring_blocks(self) -> list[str]:
        """Возвращает список блоков publication.pravo.gov.ru для мониторинга."""

        return [
            block.strip()
            for block in self.publication_pravo_monitoring_blocks.split(",")
            if block.strip()
        ]


class PravoEbpiSettings(SettingsBase):
    """Настройки клиента банка консолидированных редакций actual.pravo.gov.ru.

    Контракт API реконструирован по фронтенду портала и зафиксирован в
    `PRAVO_EBPI_API.md`. Базовый URL содержит нестандартный порт 8000 — это
    значение из `config.js` самого портала, а не опечатка.
    """

    pravo_ebpi_base_url: str = "http://actual.pravo.gov.ru:8000/api/ebpi"
    pravo_ebpi_bank: str = "ebpi"
    pravo_ebpi_timeout_seconds: int = 120
    # Портал отдаёт кодекс целиком одним ответом (ТК РФ — около 2 МБ), поэтому
    # таймаут заметно больше, чем у publication.pravo.gov.ru.
    pravo_ebpi_search_start_date: str = "20220701"
    pravo_ebpi_max_retries: int = 3
    pravo_ebpi_retry_delay_seconds: float = 2.0
    pravo_ebpi_max_concurrent_requests: int = 2


class RagSettings(SettingsBase):
    """Настройки клиента RAG Service."""

    rag_service_base_url: str = "http://localhost:8002"
    rag_service_api_key: SecretStr | None = None
    rag_service_timeout_seconds: int = 60


class SchedulerSettings(SettingsBase):
    """Настройки фонового планировщика."""

    scheduler_enabled: bool = True
    monitoring_cron_hour: int = 3
    processing_cron_hour: int = 4
    timezone: str = "Europe/Moscow"
    processing_max_retries: int = 3


class Settings(BaseSettings):
    """Агрегированные настройки проекта."""

    app: AppSettings = Field(default_factory=AppSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    publication_pravo: PublicationPravoSettings = Field(default_factory=PublicationPravoSettings)
    pravo_ebpi: PravoEbpiSettings = Field(default_factory=PravoEbpiSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)


@lru_cache
def get_settings() -> Settings:
    """Возвращает кешированный экземпляр настроек приложения."""

    return Settings()
