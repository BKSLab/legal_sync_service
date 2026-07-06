from typing import Annotated

from fastapi import Depends

from app.dependencies.repositories import (
    LegalChangesRepositoryDep,
    TrackedDocumentsRepositoryDep,
)
from app.services.legal_changes import LegalChangesService
from app.services.tracked_documents import TrackedDocumentsService


def get_tracked_documents_service(
    repository: TrackedDocumentsRepositoryDep,
) -> TrackedDocumentsService:
    """Фабрика сервиса отслеживаемых документов."""

    return TrackedDocumentsService(repository=repository)


TrackedDocumentsServiceDep = Annotated[
    TrackedDocumentsService,
    Depends(get_tracked_documents_service),
]


def get_legal_changes_service(
    legal_changes_repository: LegalChangesRepositoryDep,
    tracked_documents_repository: TrackedDocumentsRepositoryDep,
) -> LegalChangesService:
    """Фабрика сервиса событий изменений."""

    return LegalChangesService(
        legal_changes_repository=legal_changes_repository,
        tracked_documents_repository=tracked_documents_repository,
    )


LegalChangesServiceDep = Annotated[
    LegalChangesService,
    Depends(get_legal_changes_service),
]
