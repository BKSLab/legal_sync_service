import pytest
from app.db.models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container():
    """Изолированная PostgreSQL; настройки БД приложения не используются."""
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
async def session_factory(postgres_container):
    engine = create_async_engine(postgres_container.get_connection_url().replace("psycopg2", "asyncpg"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
