from datetime import datetime

from app.db.models.base import Base, TimestampMixin
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class AutomationCase(TimestampMixin, Base):
    """Проверка всей редакции, включая случаи без найденных событий."""

    __tablename__ = "automation_cases"
    __table_args__ = (
        UniqueConstraint("tracked_document_id", "redaction_id", name="uq_automation_document_redaction"),
        Index("ix_automation_queue", "status", "next_attempt_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_document_id: Mapped[int] = mapped_column(ForeignKey("tracked_documents.id", ondelete="CASCADE"))
    redaction_id: Mapped[int] = mapped_column(Integer)
    amending_doc_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending", server_default="pending")
    stage: Mapped[str] = mapped_column(String(40), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_run_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)


class AutomationRun(Base):
    """Неизменяемые исходные данные и результат каждой попытки проверки."""

    __tablename__ = "automation_runs"
    __table_args__ = (Index("ix_automation_runs_case", "case_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("automation_cases.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30))
    stage: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    policy_version: Mapped[str] = mapped_column(String(100))
    configuration_version: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
