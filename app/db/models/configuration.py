from datetime import datetime

from app.db.models.base import Base
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class ServiceConfiguration(Base):
    """Одна общая конфигурация для всех процессов сервиса."""

    __tablename__ = "service_configuration"
    __table_args__ = (
        CheckConstraint("id = 1", name="service_configuration_singleton"),
        CheckConstraint("processing_max_retries BETWEEN 1 AND 20", name="configuration_retry_limit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    rag_delivery_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    monitoring_cron_hour: Mapped[str] = mapped_column(String(100), nullable=False)
    processing_cron_hour: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    processing_max_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)


class ConfigurationChange(Base):
    """История операционных настроек; адреса и секреты сюда не записываются."""

    __tablename__ = "configuration_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False)
