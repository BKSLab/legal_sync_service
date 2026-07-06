from app.db.models.tracked_documents import TrackedDocument
from app.exceptions.tracked_documents import (
    TrackedDocumentNotFoundError,
    TrackedDocumentRepositoryError,
    TrackedDocumentServiceError,
)
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.tracked_documents import (
    TrackedDocumentCreateRequest,
    TrackedDocumentSchema,
    TrackedDocumentsListSchema,
    TrackedDocumentUpdateRequest,
)


class TrackedDocumentsService:
    """Сервис работы с реестром отслеживаемых документов."""

    def __init__(self, repository: TrackedDocumentsRepository):
        self.repository = repository

    async def create_document(self, data: TrackedDocumentCreateRequest) -> TrackedDocumentSchema:
        """Создает отслеживаемый документ."""

        try:
            document = await self.repository.save(data)
            return TrackedDocumentSchema.model_validate(document)
        except TrackedDocumentRepositoryError as error:
            raise TrackedDocumentServiceError(str(error)) from error

    async def get_document(self, document_id: int) -> TrackedDocumentSchema:
        """Возвращает отслеживаемый документ по ID."""

        document = await self._get_existing_document(document_id=document_id)
        return TrackedDocumentSchema.model_validate(document)

    async def get_documents(
        self,
        page: int,
        page_size: int,
        is_active: bool | None,
    ) -> TrackedDocumentsListSchema:
        """Возвращает список отслеживаемых документов."""

        try:
            total = await self.repository.get_count(is_active=is_active)
            documents = await self.repository.get_list(
                page=page,
                page_size=page_size,
                is_active=is_active,
            )
            return TrackedDocumentsListSchema(
                total=total,
                page=page,
                page_size=page_size,
                items=[TrackedDocumentSchema.model_validate(item) for item in documents],
            )
        except TrackedDocumentRepositoryError as error:
            raise TrackedDocumentServiceError(str(error)) from error

    async def update_document(
        self,
        document_id: int,
        data: TrackedDocumentUpdateRequest,
    ) -> TrackedDocumentSchema:
        """Обновляет отслеживаемый документ."""

        document = await self._get_existing_document(document_id=document_id)
        values = data.model_dump(exclude_unset=True)
        if not values:
            return TrackedDocumentSchema.model_validate(document)

        try:
            updated = await self.repository.update(document=document, values=values)
            return TrackedDocumentSchema.model_validate(updated)
        except TrackedDocumentRepositoryError as error:
            raise TrackedDocumentServiceError(str(error)) from error

    async def _get_existing_document(self, document_id: int) -> TrackedDocument:
        """Возвращает документ или поднимает доменное исключение."""

        try:
            document = await self.repository.get_by_id(document_id=document_id)
        except TrackedDocumentRepositoryError as error:
            raise TrackedDocumentServiceError(str(error)) from error
        if document is None:
            raise TrackedDocumentNotFoundError(document_id=document_id)
        return document
