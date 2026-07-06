"""initial schema

Revision ID: 20260706_0001
Revises:
Create Date: 2026-07-06 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260706_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    legal_change_status = postgresql.ENUM(
        "draft",
        "approved",
        "scheduled",
        "processing",
        "sent",
        "failed",
        "cancelled",
        name="legal_change_status",
        create_type=False,
    )
    legal_change_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tracked_documents",
        sa.Column("id", sa.Integer(), nullable=False, comment="Внутренний идентификатор отслеживаемого документа."),
        sa.Column("document_id", sa.String(length=200), nullable=False, comment="Идентификатор документа в RAG Service."),
        sa.Column("short_name", sa.String(length=300), nullable=False, comment="Краткое название для поиска публикаций на publication.pravo.gov.ru."),
        sa.Column("full_title", sa.Text(), nullable=False, comment="Полное наименование нормативного акта."),
        sa.Column("category", sa.String(length=200), nullable=False, comment="Категория документа для передачи в RAG Service."),
        sa.Column("audience", sa.String(length=200), nullable=False, comment="Целевая аудитория документа."),
        sa.Column("topic", sa.String(length=200), nullable=False, comment="Тема документа."),
        sa.Column("source_title", sa.String(length=300), nullable=False, comment="Название источника для RAG Service."),
        sa.Column("publication_block", sa.String(length=100), nullable=False, comment="Блок публикации на publication.pravo.gov.ru."),
        sa.Column("consolidated_source_url", sa.Text(), nullable=False, comment="URL источника актуальной консолидированной редакции."),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False, comment="Флаг активности мониторинга документа."),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="Дата и время создания записи."),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="Дата и время последнего обновления записи."),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )

    op.create_table(
        "legal_changes",
        sa.Column("id", sa.Integer(), nullable=False, comment="Внутренний идентификатор события изменения."),
        sa.Column("tracked_document_id", sa.Integer(), nullable=False, comment="Ссылка на отслеживаемый документ."),
        sa.Column("section_number", sa.String(length=100), nullable=False, comment="Номер изменяемой статьи или структурной единицы."),
        sa.Column("section_title", sa.Text(), nullable=True, comment="Заголовок изменяемой статьи."),
        sa.Column("amending_law_ref", sa.String(length=300), nullable=False, comment="eoNumber или URL закона-поправки."),
        sa.Column("adoption_date", sa.Date(), nullable=True, comment="Дата подписания закона-поправки."),
        sa.Column("effective_date", sa.Date(), nullable=True, comment="Дата вступления изменений в силу."),
        sa.Column("change_description", sa.Text(), nullable=True, comment="Краткое описание изменения."),
        sa.Column("delta_text", sa.Text(), nullable=True, comment="Фрагмент текста закона-поправки."),
        sa.Column("consolidated_text", sa.Text(), nullable=True, comment="Извлеченный текст актуальной редакции статьи."),
        sa.Column("consolidated_text_source", sa.Text(), nullable=True, comment="Источник консолидированного текста."),
        sa.Column("audience_override", sa.String(length=200), nullable=True, comment="Переопределение аудитории для RAG Service."),
        sa.Column("topic_override", sa.String(length=200), nullable=True, comment="Переопределение темы для RAG Service."),
        sa.Column("source_title_override", sa.String(length=300), nullable=True, comment="Переопределение источника для RAG Service."),
        sa.Column("status", legal_change_status, server_default="draft", nullable=False, comment="Текущий статус обработки события."),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=True, comment="Дата и время запланированной отправки в RAG Service."),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True, comment="Пользователь, подтвердивший событие."),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="Дата и время ручной проверки."),
        sa.Column("review_notes", sa.Text(), nullable=True, comment="Примечания оператора."),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True, comment="Фактическая дата отправки в RAG Service."),
        sa.Column("rag_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="Ответ RAG Service."),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False, comment="Количество попыток отправки."),
        sa.Column("last_error", sa.Text(), nullable=True, comment="Текст последней ошибки обработки."),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="Дата и время создания записи."),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="Дата и время последнего обновления записи."),
        sa.ForeignKeyConstraint(["tracked_document_id"], ["tracked_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracked_document_id", "amending_law_ref", "section_number", name="unique_legal_changes_document_law_section"),
    )


def downgrade() -> None:
    op.drop_table("legal_changes")
    op.drop_table("tracked_documents")
    postgresql.ENUM(name="legal_change_status").drop(op.get_bind(), checkfirst=True)
