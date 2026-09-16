"""Общая конфигурация и история её изменений.

Revision ID: 20260916_0003
Revises: 20260907_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0003"
down_revision: str | None = "20260907_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Значения окружения импортируются один раз приложением после миграции.
    op.create_table(
        "service_configuration",
        sa.Column("id", sa.Integer(), autoincrement=False, primary_key=True),
        sa.Column("rag_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False),
        sa.Column("monitoring_cron_hour", sa.String(100), nullable=False),
        sa.Column("processing_cron_hour", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("processing_max_retries", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.CheckConstraint("id = 1", name="service_configuration_singleton"),
        sa.CheckConstraint("processing_max_retries BETWEEN 1 AND 20", name="configuration_retry_limit"),
    )
    op.create_table(
        "configuration_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("changed_by", sa.String(200), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("configuration_changes")
    op.drop_table("service_configuration")
