from sqlalchemy import text

from app.db.session import async_session_factory


async def check_db_connection() -> None:
    """Проверяет доступность PostgreSQL."""

    async with async_session_factory() as session:
        await session.execute(text("SELECT 1"))
