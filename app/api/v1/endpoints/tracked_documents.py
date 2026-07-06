import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.dependencies.auth import VerifyApiKeyDep
from app.dependencies.services import TrackedDocumentsServiceDep
from app.exceptions.tracked_documents import (
    TrackedDocumentAlreadyExistsError,
    TrackedDocumentNotFoundError,
    TrackedDocumentServiceError,
)
from app.schemas.tracked_documents import (
    TrackedDocumentCreateRequest,
    TrackedDocumentSchema,
    TrackedDocumentsListSchema,
    TrackedDocumentUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracked-documents", tags=["tracked-documents"])


@router.post(
    path="",
    status_code=status.HTTP_201_CREATED,
    response_model=TrackedDocumentSchema,
    summary="Создать отслеживаемый документ",
    description="Добавляет нормативный акт в реестр мониторинга.",
    operation_id="createTrackedDocument",
    response_description="Созданный отслеживаемый документ.",
)
async def create_tracked_document(
    data: TrackedDocumentCreateRequest,
    service: TrackedDocumentsServiceDep,
    _: VerifyApiKeyDep,
) -> TrackedDocumentSchema:
    """Создает отслеживаемый документ."""

    logger.info("🚀 Запрос POST /tracked-documents. document_id=%s", data.document_id)
    try:
        result = await service.create_document(data=data)
        logger.info("✅ Документ создан. id=%s", result.id)
        return result
    except (TrackedDocumentAlreadyExistsError, TrackedDocumentServiceError) as error:
        logger.exception("❌ Ошибка создания документа: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="",
    response_model=TrackedDocumentsListSchema,
    summary="Получить реестр документов",
    description="Возвращает отслеживаемые документы с пагинацией и фильтром активности.",
    operation_id="getTrackedDocuments",
    response_description="Пагинированный список документов.",
)
async def get_tracked_documents(
    service: TrackedDocumentsServiceDep,
    _: VerifyApiKeyDep,
    page: Annotated[int, Query(ge=1, description="Номер страницы.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Размер страницы.")] = 20,
    is_active: Annotated[bool | None, Query(description="Фильтр активности.")] = None,
) -> TrackedDocumentsListSchema:
    """Возвращает список отслеживаемых документов."""

    logger.info("🚀 Запрос GET /tracked-documents.")
    try:
        result = await service.get_documents(page=page, page_size=page_size, is_active=is_active)
        logger.info("✅ Запрос GET /tracked-documents выполнен. total=%s", result.total)
        return result
    except TrackedDocumentServiceError as error:
        logger.exception("❌ Ошибка получения списка документов: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{document_id}",
    response_model=TrackedDocumentSchema,
    summary="Получить отслеживаемый документ",
    description="Возвращает один документ реестра по внутреннему ID.",
    operation_id="getTrackedDocument",
    response_description="Отслеживаемый документ.",
)
async def get_tracked_document(
    document_id: Annotated[int, Path(ge=1, description="Внутренний ID документа.")],
    service: TrackedDocumentsServiceDep,
    _: VerifyApiKeyDep,
) -> TrackedDocumentSchema:
    """Возвращает отслеживаемый документ."""

    logger.info("🚀 Запрос GET /tracked-documents/%s.", document_id)
    try:
        result = await service.get_document(document_id=document_id)
        logger.info("✅ Документ получен. id=%s", result.id)
        return result
    except (TrackedDocumentNotFoundError, TrackedDocumentServiceError) as error:
        logger.exception("❌ Ошибка получения документа: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/{document_id}",
    response_model=TrackedDocumentSchema,
    summary="Обновить отслеживаемый документ",
    description="Частично обновляет настройки документа в реестре мониторинга.",
    operation_id="updateTrackedDocument",
    response_description="Обновленный отслеживаемый документ.",
)
async def update_tracked_document(
    document_id: Annotated[int, Path(ge=1, description="Внутренний ID документа.")],
    data: TrackedDocumentUpdateRequest,
    service: TrackedDocumentsServiceDep,
    _: VerifyApiKeyDep,
) -> TrackedDocumentSchema:
    """Обновляет отслеживаемый документ."""

    logger.info("🚀 Запрос PATCH /tracked-documents/%s.", document_id)
    try:
        result = await service.update_document(document_id=document_id, data=data)
        logger.info("✅ Документ обновлен. id=%s", result.id)
        return result
    except (
        TrackedDocumentAlreadyExistsError,
        TrackedDocumentNotFoundError,
        TrackedDocumentServiceError,
    ) as error:
        logger.exception("❌ Ошибка обновления документа: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
