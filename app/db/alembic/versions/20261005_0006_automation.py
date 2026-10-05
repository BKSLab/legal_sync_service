"""Проверка законов, протокол решений и фиксация проверенного текста."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261005_0006"
down_revision = "20260921_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_configuration", sa.Column("automation_mode", sa.String(20), nullable=False, server_default="manual"))
    op.add_column("service_configuration", sa.Column("verification_model", sa.String(200), nullable=False, server_default="google/gemini-3.5-flash-lite"))
    for name, length in (("review_origin", 20), ("verified_text_sha256", 64), ("verified_payload_sha256", 64)):
        op.add_column("legal_changes", sa.Column(name, sa.String(length)))
    op.add_column("legal_changes", sa.Column("automation_run_id", sa.Integer()))
    op.create_table(
        "automation_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracked_document_id", sa.Integer(), sa.ForeignKey("tracked_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redaction_id", sa.Integer(), nullable=False),
        sa.Column("amending_doc_hash", sa.String(64)),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("stage", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("latest_run_id", sa.Integer()),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="Дата и время создания записи."),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="Дата и время последнего обновления записи."),
        sa.UniqueConstraint("tracked_document_id", "redaction_id", name="uq_automation_document_redaction"),
    )
    op.create_index("ix_automation_queue", "automation_cases", ["status", "next_attempt_at"])
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("automation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_sha256", sa.String(64)),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_automation_runs_case", "automation_runs", ["case_id", "id"])


def downgrade():
    op.drop_table("automation_runs")
    op.drop_table("automation_cases")
    for name in ("review_origin", "automation_run_id", "verified_text_sha256", "verified_payload_sha256"):
        op.drop_column("legal_changes", name)
    op.drop_column("service_configuration", "verification_model")
    op.drop_column("service_configuration", "automation_mode")
