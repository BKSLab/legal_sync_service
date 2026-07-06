from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/"
            f"{self.postgres_name}"
        )

    @property
    def sync_url_connect(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/"
            f"{self.postgres_name}"
        )


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

    publication_pravo_base_url: str = "https://publication.pravo.gov.ru"
    publication_pravo_timeout_seconds: int = 30


class RagSettings(SettingsBase):
    """Настройки клиента RAG Service."""

    rag_service_base_url: str = "http://localhost:8001"
    rag_service_api_key: SecretStr | None = None
    rag_service_timeout_seconds: int = 60


class SchedulerSettings(SettingsBase):
    """Настройки фонового планировщика."""

    scheduler_enabled: bool = True
    monitoring_cron_hour: int = 3
    processing_cron_hour: int = 4
    timezone: str = "Europe/Moscow"


class Settings(BaseSettings):
    """Агрегированные настройки проекта."""

    app: AppSettings = Field(default_factory=AppSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    publication_pravo: PublicationPravoSettings = Field(default_factory=PublicationPravoSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)


@lru_cache
def get_settings() -> Settings:
    """Возвращает кешированный экземпляр настроек приложения."""

    return Settings()
