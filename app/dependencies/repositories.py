from typing import Annotated

from fastapi import Depends

from app.dependencies.db_session import DbSessionDep
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository


def get_tracked_documents_repository(session: DbSessionDep) -> TrackedDocumentsRepository:
    """Фабрика репозитория отслеживаемых документов."""

    return TrackedDocumentsRepository(db_session=session)


TrackedDocumentsRepositoryDep = Annotated[
    TrackedDocumentsRepository,
    Depends(get_tracked_documents_repository),
]


def get_legal_changes_repository(session: DbSessionDep) -> LegalChangesRepository:
    """Фабрика репозитория событий изменений."""

    return LegalChangesRepository(db_session=session)


LegalChangesRepositoryDep = Annotated[
    LegalChangesRepository,
    Depends(get_legal_changes_repository),
]
