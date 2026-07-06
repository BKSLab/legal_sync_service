from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.tracked_documents import TrackedDocument
from app.exceptions.tracked_documents import (
    TrackedDocumentAlreadyExistsError,
    TrackedDocumentRepositoryError,
)
from app.schemas.tracked_documents import TrackedDocumentCreateRequest


class TrackedDocumentsRepository:
    """Репозиторий отслеживаемых документов."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def save(self, data: TrackedDocumentCreateRequest) -> TrackedDocument:
        """Сохраняет новый отслеживаемый документ."""

        document = TrackedDocument(**data.model_dump())
        try:
            self.db_session.add(document)
            await self.db_session.commit()
            await self.db_session.refresh(document)
            return document
        except IntegrityError as error:
            await self.db_session.rollback()
            raise TrackedDocumentAlreadyExistsError(document_id=data.document_id) from error
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TrackedDocumentRepositoryError(str(error)) from error

    async def get_by_id(self, document_id: int) -> TrackedDocument | None:
        """Возвращает документ по внутреннему ID."""

        try:
            result = await self.db_session.execute(
                select(TrackedDocument).where(TrackedDocument.id == document_id)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise TrackedDocumentRepositoryError(str(error)) from error

    async def get_by_document_id(self, document_id: str) -> TrackedDocument | None:
        """Возвращает документ по ID на стороне RAG."""

        try:
            result = await self.db_session.execute(
                select(TrackedDocument).where(TrackedDocument.document_id == document_id)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise TrackedDocumentRepositoryError(str(error)) from error

    async def get_list(
        self,
        page: int,
        page_size: int,
        is_active: bool | None = None,
    ) -> list[TrackedDocument]:
        """Возвращает список документов с пагинацией."""

        stmt = select(TrackedDocument).order_by(TrackedDocument.id)
        if is_active is not None:
            stmt = stmt.where(TrackedDocument.is_active == is_active)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        try:
            result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            raise TrackedDocumentRepositoryError(str(error)) from error

    async def get_count(self, is_active: bool | None = None) -> int:
        """Возвращает количество документов."""

        stmt = select(func.count()).select_from(TrackedDocument)
        if is_active is not None:
            stmt = stmt.where(TrackedDocument.is_active == is_active)

        try:
            result = await self.db_session.execute(stmt)
            return int(result.scalar_one())
        except SQLAlchemyError as error:
            raise TrackedDocumentRepositoryError(str(error)) from error

    async def update(self, document: TrackedDocument, values: dict) -> TrackedDocument:
        """Обновляет документ."""

        for field, value in values.items():
            setattr(document, field, value)

        try:
            await self.db_session.commit()
            await self.db_session.refresh(document)
            return document
        except IntegrityError as error:
            await self.db_session.rollback()
            raise TrackedDocumentAlreadyExistsError(document_id=document.document_id) from error
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TrackedDocumentRepositoryError(str(error)) from error
