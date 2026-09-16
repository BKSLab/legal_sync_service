"""Журнал запусков мониторинга, документов и этапов проверки.

Revision ID: 20260916_0004
Revises: 20260916_0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260916_0004"
down_revision = "20260916_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), unique=True),
        sa.Column("worker", sa.String(200), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in (
            "documents_total", "documents_checked", "documents_failed", "changes_created", "warnings_count",
        )),
        sa.Column("configuration", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("error_traceback", sa.Text()),
    )
    op.create_index("ix_monitoring_runs_status", "monitoring_runs", ["status"])
    op.create_index("ix_monitoring_runs_started_at", "monitoring_runs", ["started_at"])
    op.create_table(
        "monitoring_document_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tracked_document_id", sa.Integer(), sa.ForeignKey("tracked_documents.id", ondelete="SET NULL")),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("document_title", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in (
            "redactions_total", "redactions_pending", "redactions_skipped_incomplete", "changes_created", "warnings_count",
        )),
        sa.Column("error", sa.Text()),
        sa.Column("error_traceback", sa.Text()),
    )
    op.create_index("ix_monitoring_document_checks_run_id_id", "monitoring_document_checks", ["run_id", "id"])
    op.create_index("ix_monitoring_document_checks_document_id", "monitoring_document_checks", ["document_id"])
    op.create_table(
        "monitoring_log_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("monitoring_document_checks.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("stage", sa.String(60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB()),
    )
    op.create_index("ix_monitoring_log_entries_run_id_id", "monitoring_log_entries", ["run_id", "id"])


def downgrade() -> None:
    op.drop_table("monitoring_log_entries")
    op.drop_table("monitoring_document_checks")
    op.drop_table("monitoring_runs")
