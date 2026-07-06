from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.schemas.legal_changes import LegalChangeCreateRequest


class LegalChangesRepository:
    """Репозиторий событий изменений."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def save(self, data: LegalChangeCreateRequest) -> LegalChange:
        """Сохраняет новое событие изменения."""

        values = data.model_dump()
        if values.get("effective_date"):
            values["send_at"] = datetime.combine(values["effective_date"], time.min, tzinfo=UTC)

        change = LegalChange(**values)
        try:
            self.db_session.add(change)
            await self.db_session.commit()
            await self.db_session.refresh(change)
            return change
        except IntegrityError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError("Событие уже существует.") from error
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_by_id(self, change_id: int) -> LegalChange | None:
        """Возвращает событие по ID."""

        try:
            result = await self.db_session.execute(
                select(LegalChange).where(LegalChange.id == change_id)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_list(
        self,
        page: int,
        page_size: int,
        status: LegalChangeStatus | None = None,
    ) -> list[LegalChange]:
        """Возвращает список событий с пагинацией."""

        stmt = select(LegalChange).order_by(LegalChange.id.desc())
        if status is not None:
            stmt = stmt.where(LegalChange.status == status)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        try:
            result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_count(self, status: LegalChangeStatus | None = None) -> int:
        """Возвращает количество событий."""

        stmt = select(func.count()).select_from(LegalChange)
        if status is not None:
            stmt = stmt.where(LegalChange.status == status)

        try:
            result = await self.db_session.execute(stmt)
            return int(result.scalar_one())
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def update(self, change: LegalChange, values: dict) -> LegalChange:
        """Обновляет событие изменения."""

        for field, value in values.items():
            setattr(change, field, value)

        try:
            await self.db_session.commit()
            await self.db_session.refresh(change)
            return change
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error
