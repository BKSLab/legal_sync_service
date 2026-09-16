from datetime import datetime

from app.db.models.base import Base
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class MonitoringRun(Base):
    """История запусков; хранится независимо от транзакций с событиями изменений."""

    __tablename__ = "monitoring_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), unique=True)
    worker: Mapped[str] = mapped_column(String(200))
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_checked: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    changes_created: Mapped[int] = mapped_column(Integer, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    configuration: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)

    @property
    def duration_seconds(self) -> float | None:
        return (self.finished_at - self.started_at).total_seconds() if self.finished_at else None


class MonitoringDocumentCheck(Base):
    """Снимок документа и результат его проверки, включая частично выполненную."""

    __tablename__ = "monitoring_document_checks"
    __table_args__ = (Index("ix_monitoring_document_checks_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="CASCADE"))
    tracked_document_id: Mapped[int | None] = mapped_column(ForeignKey("tracked_documents.id", ondelete="SET NULL"))
    document_id: Mapped[str] = mapped_column(Text, index=True)
    document_title: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redactions_total: Mapped[int] = mapped_column(Integer, default=0)
    redactions_pending: Mapped[int] = mapped_column(Integer, default=0)
    redactions_skipped_incomplete: Mapped[int] = mapped_column(Integer, default=0)
    changes_created: Mapped[int] = mapped_column(Integer, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)


class MonitoringLogEntry(Base):
    """Последовательность этапов, решений, запросов и ошибок одного запуска."""

    __tablename__ = "monitoring_log_entries"
    __table_args__ = (Index("ix_monitoring_log_entries_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="CASCADE"))
    check_id: Mapped[int | None] = mapped_column(ForeignKey("monitoring_document_checks.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[str] = mapped_column(String(10))
    stage: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
