from datetime import date, datetime
from enum import StrEnum

from app.db.models.base import Base, TimestampMixin
from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    monitoring_check_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        # Единица дедупликации — редакция, а не акт-поправка: один закон может
        # менять одну и ту же статью в нескольких редакциях с разными датами
        # вступления в силу, и это разные события.
        UniqueConstraint(
            "tracked_document_id",
            "ebpi_redaction_id",
            "section_number",
            name="unique_legal_changes_document_redaction_section",
        ),
        Index(
            "ix_legal_changes_status_send_at",
            "status",
            "send_at",
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
    amending_doc_hash: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        index=True,
        doc="Идентификатор акта-поправки в банке редакций.",
        comment=(
            "Идентификатор акта-поправки в банке редакций. Сопоставление идёт только "
            "по нему: номер закона повторяется в разные годы."
        ),
    )
    ebpi_redaction_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        doc="Идентификатор редакции отслеживаемого документа.",
        comment="Идентификатор редакции отслеживаемого документа в банке редакций.",
    )
    redaction_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Дата вступления редакции в силу по данным портала.",
        comment="Официальная дата вступления редакции в силу по данным банка редакций.",
    )
    amending_act_type: Mapped[str | None] = mapped_column(
        String(length=120),
        nullable=True,
        doc="Вид акта-поправки.",
        comment="Вид акта-поправки по данным карточки банка редакций.",
    )
    amending_act_number: Mapped[str | None] = mapped_column(
        String(length=100),
        nullable=True,
        doc="Номер акта-поправки.",
        comment="Номер акта-поправки по данным карточки банка редакций.",
    )
    amending_act_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Дата подписания акта-поправки.",
        comment="Дата подписания акта-поправки по данным карточки банка редакций.",
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
    topics_override: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Переопределение тем для RAG.",
        comment="Переопределение тем для RAG Service.",
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

    def __str__(self) -> str:
        return f"Событие №{self.id} · статья {self.section_number}"
