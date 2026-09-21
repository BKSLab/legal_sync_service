from datetime import datetime

from app.db.models.base import Base
from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class DeliveryAttempt(Base):
    """Одна попытка доставки. ID также передаётся RAG как X-Request-ID.

    Без каскадных связей: история остаётся после удаления документа/события.
    """

    __tablename__ = 'delivery_attempts'
    __table_args__ = (
        Index('ix_delivery_attempts_document_started', 'document_id', 'started_at'),
        Index('ix_delivery_attempts_change_started', 'change_id', 'started_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(Text)
    change_id: Mapped[int]
    section_number: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
