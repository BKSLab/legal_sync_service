from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.configuration import ConfigurationChange, ServiceConfiguration
from app.exceptions.configuration import ConfigurationConflictError
from app.schemas.configuration import (
    ConfigurationHistoryEntry,
    ConfigurationSnapshot,
    ConfigurationValues,
)


class ConfigurationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, defaults: ConfigurationValues) -> ConfigurationSnapshot:
        statement = select(ServiceConfiguration).where(ServiceConfiguration.id == 1).execution_options(populate_existing=True)
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            # Несколько воркеров могут впервые запуститься одновременно.
            await self.session.execute(
                insert(ServiceConfiguration).values(
                    id=1, **defaults.model_dump(), version=1, updated_by="Начальная конфигурация сервера",
                ).on_conflict_do_nothing(index_elements=["id"]),
            )
            await self.session.commit()
            row = (await self.session.execute(statement)).scalar_one()
        return ConfigurationSnapshot.model_validate(row)

    async def save(
        self, values: ConfigurationValues, expected_version: int, actor: str,
    ) -> ConfigurationSnapshot:
        row = (await self.session.execute(
            select(ServiceConfiguration).where(ServiceConfiguration.id == 1)
            .with_for_update().execution_options(populate_existing=True),
        )).scalar_one()
        if row.version != expected_version:
            await self.session.rollback()
            raise ConfigurationConflictError
        changes = {
            name: {"before": getattr(row, name), "after": value}
            for name, value in values.model_dump().items() if getattr(row, name) != value
        }
        if changes:
            for name, value in values.model_dump().items():
                setattr(row, name, value)
            row.version += 1
            row.updated_by = actor
            row.updated_at = datetime.now(UTC)
            self.session.add(ConfigurationChange(version=row.version, changed_by=actor, changes=changes))
        snapshot = ConfigurationSnapshot.model_validate(row)
        await self.session.commit()
        return snapshot

    async def history(self, limit: int = 20) -> list[ConfigurationHistoryEntry]:
        rows = (await self.session.execute(
            select(ConfigurationChange).order_by(ConfigurationChange.id.desc()).limit(limit),
        )).scalars()
        return [ConfigurationHistoryEntry.model_validate(row) for row in rows]
