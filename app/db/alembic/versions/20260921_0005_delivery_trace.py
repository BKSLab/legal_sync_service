"""Связи между сервисами и история попыток доставки; без удаления данных."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '20260921_0005'
down_revision = '20260916_0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracked_documents', sa.Column('rag_ingestion_id', sa.String(36)))
    op.add_column('legal_changes', sa.Column('monitoring_check_id', sa.Integer()))
    op.create_table(
        'delivery_attempts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.Text(), nullable=False),
        sa.Column('change_id', sa.Integer(), nullable=False),
        sa.Column('section_number', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('stage', sa.String(40), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.Column('error', sa.Text()),
    )
    op.create_index('ix_delivery_attempts_document_started', 'delivery_attempts', ['document_id', 'started_at'])
    op.create_index('ix_delivery_attempts_change_started', 'delivery_attempts', ['change_id', 'started_at'])


def downgrade():
    op.drop_table('delivery_attempts')
    op.drop_column('legal_changes', 'monitoring_check_id')
    op.drop_column('tracked_documents', 'rag_ingestion_id')
