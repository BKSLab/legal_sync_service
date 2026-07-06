from typing import TYPE_CHECKING

from app.db.models.base import Base, TimestampMixin
from sqlalchemy import Boolean, String, Text
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
    topic: Mapped[str] = mapped_column(
        String(length=200),
        nullable=False,
        doc="Тема документа.",
        comment="Тема документа.",
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
    consolidated_source_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="URL источника актуальной консолидированной редакции.",
        comment="URL источника актуальной консолидированной редакции.",
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
