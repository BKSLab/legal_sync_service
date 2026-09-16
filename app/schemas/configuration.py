from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigurationValues(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    rag_delivery_enabled: bool = False
    monitoring_enabled: bool = True
    monitoring_cron_hour: str = Field("*", min_length=1, max_length=100)
    processing_cron_hour: str = Field("4", min_length=1, max_length=100)
    timezone: str = Field("Europe/Moscow", min_length=1, max_length=100)
    processing_max_retries: int = Field(3, ge=1, le=20)

    @field_validator("monitoring_cron_hour", "processing_cron_hour")
    @classmethod
    def validate_hours(cls, value: str) -> str:
        value = value.strip()
        CronTrigger(hour=value, minute=0, timezone="UTC")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Укажите существующий часовой пояс, например Europe/Moscow.") from error
        return value


class ConfigurationSnapshot(ConfigurationValues):
    version: int
    updated_at: datetime
    updated_by: str


class ConfigurationHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    changed_at: datetime
    changed_by: str
    changes: dict
