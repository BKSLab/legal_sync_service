"""Поля для мониторинга через банк консолидированных редакций

Revision ID: 20260907_0002
Revises: 20260706_0001
Create Date: 2026-09-07 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0002"
down_revision: str | None = "20260706_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Блок tracked_documents: реквизиты для поиска в банке редакций и граница мониторинга.
    op.add_column(
        "tracked_documents",
        sa.Column(
            "document_number",
            sa.String(length=100),
            nullable=False,
            server_default="",
            comment="Номер акта для поиска в банке консолидированных редакций.",
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "adoption_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
            comment="Дата подписания акта; используется для проверки найденной карточки.",
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "ebpi_doc_hash",
            sa.String(length=64),
            nullable=True,
            comment="Идентификатор акта в банке редакций actual.pravo.gov.ru; определяется один раз.",
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "ebpi_doc_id",
            sa.Integer(),
            nullable=True,
            comment="Внутренний числовой идентификатор акта в банке редакций.",
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "monitor_from",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
            comment=(
                "Дата постановки на контроль. Редакции, вступившие в силу раньше, "
                "событий не порождают — иначе постановка кодекса на контроль создала бы "
                "сотни событий по всей его истории."
            ),
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Темы документа для RAG Service; допустимы только для other_npa.",
        ),
    )
    op.drop_column("tracked_documents", "topic")
    op.drop_column("tracked_documents", "consolidated_source_url")
    op.create_index(
        "ix_tracked_documents_ebpi_doc_hash",
        "tracked_documents",
        ["ebpi_doc_hash"],
    )

    # Блок legal_changes: привязка события к конкретной редакции документа.
    op.add_column(
        "legal_changes",
        sa.Column(
            "amending_doc_hash",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Идентификатор акта-поправки в банке редакций. Сопоставление идёт только "
                "по нему: номер закона повторяется в разные годы."
            ),
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "ebpi_redaction_id",
            sa.Integer(),
            nullable=True,
            comment="Идентификатор редакции отслеживаемого документа в банке редакций.",
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "redaction_date",
            sa.Date(),
            nullable=True,
            comment="Официальная дата вступления редакции в силу по данным банка редакций.",
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "amending_act_type",
            sa.String(length=120),
            nullable=True,
            comment="Вид акта-поправки по данным карточки банка редакций.",
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "amending_act_number",
            sa.String(length=100),
            nullable=True,
            comment="Номер акта-поправки по данным карточки банка редакций.",
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "amending_act_date",
            sa.Date(),
            nullable=True,
            comment="Дата подписания акта-поправки по данным карточки банка редакций.",
        ),
    )
    op.add_column(
        "legal_changes",
        sa.Column(
            "topics_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Переопределение тем для RAG Service.",
        ),
    )
    op.drop_column("legal_changes", "topic_override")
    op.create_index(
        "ix_legal_changes_amending_doc_hash",
        "legal_changes",
        ["amending_doc_hash"],
    )
    op.create_index(
        "ix_legal_changes_ebpi_redaction_id",
        "legal_changes",
        ["ebpi_redaction_id"],
    )
    op.create_index(
        "ix_legal_changes_status_send_at",
        "legal_changes",
        ["status", "send_at"],
    )
    # Один закон может менять одну статью в нескольких редакциях с разными
    # датами вступления в силу — это разные события, поэтому ключ дедупликации
    # переезжает с акта-поправки на редакцию.
    op.drop_constraint(
        "unique_legal_changes_document_law_section",
        "legal_changes",
        type_="unique",
    )
    op.create_unique_constraint(
        "unique_legal_changes_document_redaction_section",
        "legal_changes",
        ["tracked_document_id", "ebpi_redaction_id", "section_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "unique_legal_changes_document_redaction_section",
        "legal_changes",
        type_="unique",
    )
    op.create_unique_constraint(
        "unique_legal_changes_document_law_section",
        "legal_changes",
        ["tracked_document_id", "amending_law_ref", "section_number"],
    )
    op.drop_index("ix_legal_changes_status_send_at", table_name="legal_changes")
    op.drop_index("ix_legal_changes_ebpi_redaction_id", table_name="legal_changes")
    op.drop_index("ix_legal_changes_amending_doc_hash", table_name="legal_changes")
    op.add_column(
        "legal_changes",
        sa.Column(
            "topic_override",
            sa.String(length=200),
            nullable=True,
            comment="Переопределение темы для RAG Service.",
        ),
    )
    op.drop_column("legal_changes", "topics_override")
    op.drop_column("legal_changes", "amending_act_date")
    op.drop_column("legal_changes", "amending_act_number")
    op.drop_column("legal_changes", "amending_act_type")
    op.drop_column("legal_changes", "redaction_date")
    op.drop_column("legal_changes", "ebpi_redaction_id")
    op.drop_column("legal_changes", "amending_doc_hash")

    op.drop_index("ix_tracked_documents_ebpi_doc_hash", table_name="tracked_documents")
    op.add_column(
        "tracked_documents",
        sa.Column(
            "consolidated_source_url",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="URL источника актуальной консолидированной редакции.",
        ),
    )
    op.add_column(
        "tracked_documents",
        sa.Column(
            "topic",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="Тема документа.",
        ),
    )
    op.drop_column("tracked_documents", "topics")
    op.drop_column("tracked_documents", "monitor_from")
    op.drop_column("tracked_documents", "ebpi_doc_id")
    op.drop_column("tracked_documents", "ebpi_doc_hash")
    op.drop_column("tracked_documents", "adoption_date")
    op.drop_column("tracked_documents", "document_number")
