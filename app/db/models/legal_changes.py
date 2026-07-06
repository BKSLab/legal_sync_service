from datetime import date, datetime
from enum import StrEnum

from app.db.models.base import Base, TimestampMixin
from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship


class LegalChangeStatus(StrEnum):
    """Статусы обработки события изменения."""

    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LegalChange(TimestampMixin, Base):
    """Модель события изменения нормативного акта."""

    __tablename__ = "legal_changes"
    __table_args__ = (
        UniqueConstraint(
            "tracked_document_id",
            "amending_law_ref",
            "section_number",
            name="unique_legal_changes_document_law_section",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        comment="Внутренний идентификатор события изменения.",
    )
    tracked_document_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_documents.id", ondelete="CASCADE"),
        nullable=False,
        doc="Ссылка на отслеживаемый документ.",
        comment="Ссылка на отслеживаемый документ.",
    )
    section_number: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Номер изменяемой статьи или структурной единицы.",
        comment="Номер изменяемой статьи или структурной единицы.",
    )
    section_title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Заголовок изменяемой статьи.",
        comment="Заголовок изменяемой статьи.",
    )
    amending_law_ref: Mapped[str] = mapped_column(
        String(length=300),
        nullable=False,
        doc="eoNumber или URL закона-поправки.",
        comment="eoNumber или URL закона-поправки.",
    )
    adoption_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Дата подписания закона-поправки.",
        comment="Дата подписания закона-поправки.",
    )
    effective_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Дата вступления изменений в силу.",
        comment="Дата вступления изменений в силу.",
    )
    change_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Краткое описание изменения.",
        comment="Краткое описание изменения.",
    )
    delta_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Фрагмент текста закона-поправки.",
        comment="Фрагмент текста закона-поправки.",
    )
    consolidated_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Извлеченный текст актуальной редакции статьи.",
        comment="Извлеченный текст актуальной редакции статьи.",
    )
    consolidated_text_source: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Источник консолидированного текста.",
        comment="Источник консолидированного текста.",
    )
    audience_override: Mapped[str | None] = mapped_column(
        String(length=200),
        nullable=True,
        doc="Переопределение аудитории для RAG.",
        comment="Переопределение аудитории для RAG Service.",
    )
    topic_override: Mapped[str | None] = mapped_column(
        String(length=200),
        nullable=True,
        doc="Переопределение темы для RAG.",
        comment="Переопределение темы для RAG Service.",
    )
    source_title_override: Mapped[str | None] = mapped_column(
        String(length=300),
        nullable=True,
        doc="Переопределение источника для RAG.",
        comment="Переопределение источника для RAG Service.",
    )
    status: Mapped[LegalChangeStatus] = mapped_column(
        SqlEnum(
            LegalChangeStatus,
            name="legal_change_status",
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        nullable=False,
        default=LegalChangeStatus.DRAFT,
        server_default=LegalChangeStatus.DRAFT.value,
        doc="Текущий статус обработки события.",
        comment="Текущий статус обработки события.",
    )
    send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Дата и время запланированной отправки в RAG.",
        comment="Дата и время запланированной отправки в RAG Service.",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(length=200),
        nullable=True,
        doc="Пользователь, подтвердивший событие.",
        comment="Пользователь, подтвердивший событие.",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Дата и время ручной проверки.",
        comment="Дата и время ручной проверки.",
    )
    review_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Примечания оператора.",
        comment="Примечания оператора.",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Фактическая дата отправки в RAG.",
        comment="Фактическая дата отправки в RAG Service.",
    )
    rag_response: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Ответ RAG Service.",
        comment="Ответ RAG Service.",
    )
    retry_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
        doc="Количество попыток отправки.",
        comment="Количество попыток отправки.",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Текст последней ошибки обработки.",
        comment="Текст последней ошибки обработки.",
    )

    tracked_document = relationship("TrackedDocument", back_populates="legal_changes")

    def __repr__(self) -> str:
        return (
            f"<LegalChange(id={self.id}, section_number='{self.section_number}', "
            f"status='{self.status}')>"
        )
