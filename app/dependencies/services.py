from typing import Annotated

from fastapi import Depends

from app.core.settings import get_settings
from app.dependencies.clients import PravoEbpiClientDep, RagClientDep
from app.dependencies.repositories import (
    LegalChangesRepositoryDep,
    TrackedDocumentsRepositoryDep,
)
from app.services.legal_changes import LegalChangesService
from app.services.monitoring import MonitoringService
from app.services.processing import ProcessingService
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


def get_monitoring_service(
    tracked_documents_repository: TrackedDocumentsRepositoryDep,
    legal_changes_repository: LegalChangesRepositoryDep,
    pravo_ebpi_client: PravoEbpiClientDep,
) -> MonitoringService:
    """Фабрика сервиса мониторинга изменений."""

    return MonitoringService(
        tracked_documents_repository=tracked_documents_repository,
        legal_changes_repository=legal_changes_repository,
        pravo_ebpi_client=pravo_ebpi_client,
    )


MonitoringServiceDep = Annotated[MonitoringService, Depends(get_monitoring_service)]


def get_processing_service(
    legal_changes_repository: LegalChangesRepositoryDep,
    pravo_ebpi_client: PravoEbpiClientDep,
    rag_client: RagClientDep,
) -> ProcessingService:
    """Фабрика сервиса отправки изменений в RAG Service."""

    settings = get_settings()
    return ProcessingService(
        legal_changes_repository=legal_changes_repository,
        pravo_ebpi_client=pravo_ebpi_client,
        rag_client=rag_client,
        max_retries=settings.scheduler.processing_max_retries,
        delivery_enabled=settings.rag.rag_delivery_enabled,
    )


ProcessingServiceDep = Annotated[ProcessingService, Depends(get_processing_service)]
