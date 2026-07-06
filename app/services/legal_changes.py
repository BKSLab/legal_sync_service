from datetime import UTC, datetime, time

from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.exceptions.legal_changes import (
    LegalChangeInvalidStatusError,
    LegalChangeNotFoundError,
    LegalChangeRepositoryError,
    LegalChangeServiceError,
)
from app.exceptions.tracked_documents import TrackedDocumentNotFoundError
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.legal_changes import (
    LegalChangeCreateRequest,
    LegalChangeReviewRequest,
    LegalChangeSchema,
    LegalChangesListSchema,
)


class LegalChangesService:
    """Сервис работы с событиями изменений."""

    def __init__(
        self,
        legal_changes_repository: LegalChangesRepository,
        tracked_documents_repository: TrackedDocumentsRepository,
    ):
        self.legal_changes_repository = legal_changes_repository
        self.tracked_documents_repository = tracked_documents_repository

    async def create_change(self, data: LegalChangeCreateRequest) -> LegalChangeSchema:
        """Создает событие изменения в статусе draft."""

        document = await self.tracked_documents_repository.get_by_id(data.tracked_document_id)
        if document is None:
            raise TrackedDocumentNotFoundError(document_id=data.tracked_document_id)

        try:
            change = await self.legal_changes_repository.save(data)
            return LegalChangeSchema.model_validate(change)
        except LegalChangeRepositoryError as error:
            raise LegalChangeServiceError(str(error)) from error

    async def get_change(self, change_id: int) -> LegalChangeSchema:
        """Возвращает событие изменения по ID."""

        change = await self._get_existing_change(change_id=change_id)
        return LegalChangeSchema.model_validate(change)

    async def get_changes(
        self,
        page: int,
        page_size: int,
        status: LegalChangeStatus | None,
    ) -> LegalChangesListSchema:
        """Возвращает список событий изменений."""

        try:
            total = await self.legal_changes_repository.get_count(status=status)
            changes = await self.legal_changes_repository.get_list(
                page=page,
                page_size=page_size,
                status=status,
            )
            return LegalChangesListSchema(
                total=total,
                page=page,
                page_size=page_size,
                items=[LegalChangeSchema.model_validate(item) for item in changes],
            )
        except LegalChangeRepositoryError as error:
            raise LegalChangeServiceError(str(error)) from error

    async def approve_change(
        self,
        change_id: int,
        data: LegalChangeReviewRequest,
    ) -> LegalChangeSchema:
        """Подтверждает событие и переводит его в scheduled."""

        change = await self._get_existing_change(change_id=change_id)
        if change.status != LegalChangeStatus.DRAFT:
            raise LegalChangeInvalidStatusError(
                change_id=change.id,
                current_status=change.status.value,
                expected_status=LegalChangeStatus.DRAFT.value,
            )

        effective_date = data.effective_date or change.effective_date
        send_at = datetime.combine(effective_date, time.min, tzinfo=UTC) if effective_date else None
        values = {
            "status": LegalChangeStatus.SCHEDULED,
            "effective_date": effective_date,
            "send_at": send_at,
            "reviewed_by": data.reviewed_by,
            "reviewed_at": datetime.now(tz=UTC),
            "review_notes": data.review_notes,
        }
        updated = await self.legal_changes_repository.update(change=change, values=values)
        return LegalChangeSchema.model_validate(updated)

    async def cancel_change(self, change_id: int, review_notes: str | None = None) -> LegalChangeSchema:
        """Отменяет событие изменения."""

        change = await self._get_existing_change(change_id=change_id)
        values = {
            "status": LegalChangeStatus.CANCELLED,
            "review_notes": review_notes,
        }
        updated = await self.legal_changes_repository.update(change=change, values=values)
        return LegalChangeSchema.model_validate(updated)

    async def _get_existing_change(self, change_id: int) -> LegalChange:
        """Возвращает событие или поднимает доменное исключение."""

        try:
            change = await self.legal_changes_repository.get_by_id(change_id=change_id)
        except LegalChangeRepositoryError as error:
            raise LegalChangeServiceError(str(error)) from error
        if change is None:
            raise LegalChangeNotFoundError(change_id=change_id)
        return change
