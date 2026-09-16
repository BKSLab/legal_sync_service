from datetime import date
from typing import TYPE_CHECKING

from app.db.models.base import Base, TimestampMixin
from sqlalchemy import Boolean, Date, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.db.models.legal_changes import LegalChange


class TrackedDocument(TimestampMixin, Base):
    """Модель отслеживаемого нормативного акта."""

    __tablename__ = "tracked_documents"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        comment="Внутренний идентификатор отслеживаемого документа.",
    )
    document_id: Mapped[str] = mapped_column(
        String(length=200),
        unique=True,
        nullable=False,
        doc="Идентификатор документа в RAG Service.",
        comment="Идентификатор документа в RAG Service.",
    )
    short_name: Mapped[str] = mapped_column(
        String(length=300),
        nullable=False,
        doc="Краткое название для поиска публикаций.",
        comment="Краткое название для поиска публикаций на publication.pravo.gov.ru.",
    )
    full_title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Полное наименование нормативного акта.",
        comment="Полное наименование нормативного акта.",
    )
    category: Mapped[str] = mapped_column(
        String(length=200),
        nullable=False,
        doc="Категория документа для передачи в RAG.",
        comment="Категория документа для передачи в RAG Service.",
    )
    audience: Mapped[str] = mapped_column(
        String(length=200),
        nullable=False,
        doc="Целевая аудитория документа.",
        comment="Целевая аудитория документа.",
    )
    topics: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        doc="Темы документа для RAG.",
        comment="Темы документа для RAG Service; допустимы только для other_npa.",
    )
    source_title: Mapped[str] = mapped_column(
        String(length=300),
        nullable=False,
        doc="Название источника для RAG.",
        comment="Название источника для RAG Service.",
    )
    publication_block: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Блок публикации на publication.pravo.gov.ru.",
        comment="Блок публикации на publication.pravo.gov.ru.",
    )
    document_number: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Номер акта, например 197-ФЗ.",
        comment="Номер акта для поиска в банке консолидированных редакций.",
    )
    adoption_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        doc="Дата подписания акта.",
        comment="Дата подписания акта; используется для проверки найденной карточки.",
    )
    ebpi_doc_hash: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        index=True,
        doc="Идентификатор акта в банке консолидированных редакций.",
        comment="Идентификатор акта в банке редакций actual.pravo.gov.ru; определяется один раз.",
    )
    ebpi_doc_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Внутренний числовой идентификатор акта в банке редакций.",
        comment="Внутренний числовой идентификатор акта в банке редакций.",
    )
    monitor_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        doc="Дата, с которой отслеживаются редакции документа.",
        comment=(
            "Дата постановки на контроль. Редакции, вступившие в силу раньше, "
            "событий не порождают — иначе постановка кодекса на контроль создала бы "
            "сотни событий по всей его истории."
        ),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        doc="Флаг активности мониторинга.",
        comment="Флаг активности мониторинга документа.",
    )

    legal_changes: Mapped[list["LegalChange"]] = relationship(
        back_populates="tracked_document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<TrackedDocument(id={self.id}, document_id='{self.document_id}', "
            f"short_name='{self.short_name}')>"
        )

    def __str__(self) -> str:
        return f"{self.short_name} ({self.document_id})"
