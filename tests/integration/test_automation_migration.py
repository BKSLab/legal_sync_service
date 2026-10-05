from types import SimpleNamespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.models import Base
from sqlalchemy import create_engine, text


def test_upgrade_preserves_settings_and_matches_models(postgres_container, monkeypatch):
    """Переход существующей БД, включая откат схемы, на изолированной PostgreSQL."""
    url = postgres_container.get_connection_url().replace("psycopg2", "psycopg")
    monkeypatch.setattr('app.core.settings.get_settings', lambda: SimpleNamespace(db=SimpleNamespace(sync_url_connect=url)))
    config = Config('alembic.ini')
    engine = create_engine(url)
    try:
        command.upgrade(config, '20260921_0005')
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO service_configuration
                (id, rag_delivery_enabled, monitoring_enabled, monitoring_cron_hour, processing_cron_hour,
                 timezone, processing_max_retries, version, updated_by)
                VALUES (1, true, true, '6', '8', 'Europe/Moscow', 3, 2, 'operator')"""))
        command.upgrade(config, 'head')
        with engine.connect() as connection:
            row = connection.execute(text('SELECT * FROM service_configuration')).mappings().one()
            assert row['version'] == 2 and row['automation_mode'] == 'manual'
            assert row['monitoring_cron_hour'] == '6' and row['processing_cron_hour'] == '8'
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        command.downgrade(config, '20260921_0005')
        command.upgrade(config, 'head')
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version FROM service_configuration')) == 2
    finally:
        command.downgrade(config, 'base')
        with engine.begin() as connection:
            connection.execute(text('DROP TABLE IF EXISTS alembic_version'))
        engine.dispose()
