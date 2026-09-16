import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.db.models.legal_changes import LegalChangeStatus
from app.dependencies.auth import VerifyApiKeyDep
from app.dependencies.services import LegalChangesServiceDep, ProcessingPreviewServiceDep
from app.exceptions.legal_changes import (
    LegalChangeInvalidStatusError,
    LegalChangeNotFoundError,
    LegalChangePreviewConflictError,
    LegalChangeRepositoryError,
    LegalChangeServiceError,
)
from app.exceptions.pravo_ebpi import PravoEbpiClientError
from app.exceptions.redaction import RedactionParseError, RedactionSectionNotFoundError
from app.exceptions.tracked_documents import TrackedDocumentNotFoundError
from app.schemas.legal_changes import (
    LegalChangeCreateRequest,
    LegalChangeReviewRequest,
    LegalChangeSchema,
    LegalChangesListSchema,
)
from app.schemas.processing_preview import ProcessingPreviewRequest, ProcessingPreviewResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/legal-changes", tags=["legal-changes"])


@router.post(
    path="",
    status_code=status.HTTP_201_CREATED,
    response_model=LegalChangeSchema,
    summary="Создать событие изменения",
    description="Создает событие изменения вручную. Автоматический мониторинг будет использовать тот же сервисный слой.",
    operation_id="createLegalChange",
    response_description="Созданное событие изменения.",
)
async def create_legal_change(
    data: LegalChangeCreateRequest,
    service: LegalChangesServiceDep,
    _: VerifyApiKeyDep,
) -> LegalChangeSchema:
    """Создает событие изменения."""

    logger.info("🚀 Запрос POST /legal-changes. tracked_document_id=%s", data.tracked_document_id)
    try:
        result = await service.create_change(data=data)
        logger.info("✅ Событие создано. id=%s", result.id)
        return result
    except (TrackedDocumentNotFoundError, LegalChangeServiceError) as error:
        logger.exception("❌ Ошибка создания события: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="",
    response_model=LegalChangesListSchema,
    summary="Получить события изменений",
    description="Возвращает события изменений с пагинацией и фильтром статуса.",
    operation_id="getLegalChanges",
    response_description="Пагинированный список событий.",
)
async def get_legal_changes(
    service: LegalChangesServiceDep,
    _: VerifyApiKeyDep,
    page: Annotated[int, Query(ge=1, description="Номер страницы.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Размер страницы.")] = 20,
    change_status: Annotated[LegalChangeStatus | None, Query(alias="status")] = None,
) -> LegalChangesListSchema:
    """Возвращает список событий изменений."""

    logger.info("🚀 Запрос GET /legal-changes.")
    try:
        result = await service.get_changes(
            page=page,
            page_size=page_size,
            status=change_status,
        )
        logger.info("✅ Запрос GET /legal-changes выполнен. total=%s", result.total)
        return result
    except LegalChangeServiceError as error:
        logger.exception("❌ Ошибка получения списка событий: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{change_id}",
    response_model=LegalChangeSchema,
    summary="Получить событие изменения",
    description="Возвращает одно событие изменения по ID.",
    operation_id="getLegalChange",
    response_description="Событие изменения.",
)
async def get_legal_change(
    change_id: Annotated[int, Path(ge=1, description="ID события изменения.")],
    service: LegalChangesServiceDep,
    _: VerifyApiKeyDep,
) -> LegalChangeSchema:
    """Возвращает событие изменения."""

    logger.info("🚀 Запрос GET /legal-changes/%s.", change_id)
    try:
        result = await service.get_change(change_id=change_id)
        logger.info("✅ Событие получено. id=%s", result.id)
        return result
    except (LegalChangeNotFoundError, LegalChangeServiceError) as error:
        logger.exception("❌ Ошибка получения события: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/{change_id}/preview",
    response_model=ProcessingPreviewResult,
    summary="Проверить извлечение статьи на условную дату",
    description=(
        "Проверяет срок одного события относительно as_of и загружает именно его редакцию. "
        "Сохраняет извлечённую статью в consolidated_text, источник — в consolidated_text_source. "
        "Работает с draft без подтверждения: статус, реальные даты и попытки отправки не меняются. "
        "RAG не вызывается при любом значении RAG_DELIVERY_ENABLED. Это проверка извлечения, "
        "а не запуск планировщика или отправки."
    ),
    operation_id="previewLegalChange",
    responses={
        404: {"description": "Событие или статья не найдены."},
        409: {"description": "Очередь занята либо состояние события не допускает проверку."},
        502: {"description": "Не удалось получить или разобрать редакцию."},
    },
)
async def preview_legal_change(
    change_id: Annotated[int, Path(ge=1, description="ID события изменения.")],
    data: ProcessingPreviewRequest,
    service: ProcessingPreviewServiceDep,
    _: VerifyApiKeyDep,
) -> ProcessingPreviewResult:
    """Выполняет пробное извлечение статьи без отправки в RAG."""

    try:
        return await service.preview_change(change_id, data)
    except (
        LegalChangeNotFoundError, LegalChangePreviewConflictError,
        PravoEbpiClientError, RedactionParseError, RedactionSectionNotFoundError,
    ) as error:
        logger.warning("Пробное извлечение не выполнено. change_id=%s причина=%s", change_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except LegalChangeRepositoryError as error:
        logger.exception("Ошибка сохранения пробного извлечения. change_id=%s", change_id)
        raise HTTPException(status_code=500, detail="Не удалось сохранить результат проверки.") from error


@router.post(
    path="/{change_id}/approve",
    response_model=LegalChangeSchema,
    summary="Подтвердить событие изменения",
    description="Переводит событие из draft в scheduled после ручной проверки.",
    operation_id="approveLegalChange",
    response_description="Подтвержденное событие изменения.",
)
async def approve_legal_change(
    change_id: Annotated[int, Path(ge=1, description="ID события изменения.")],
    data: LegalChangeReviewRequest,
    service: LegalChangesServiceDep,
    _: VerifyApiKeyDep,
) -> LegalChangeSchema:
    """Подтверждает событие изменения."""

    logger.info("🚀 Запрос POST /legal-changes/%s/approve.", change_id)
    try:
        result = await service.approve_change(change_id=change_id, data=data)
        logger.info("✅ Событие подтверждено. id=%s", result.id)
        return result
    except (
        LegalChangeInvalidStatusError,
        LegalChangeNotFoundError,
        LegalChangeServiceError,
    ) as error:
        logger.exception("❌ Ошибка подтверждения события: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/{change_id}/cancel",
    response_model=LegalChangeSchema,
    summary="Отменить событие изменения",
    description="Переводит событие в cancelled.",
    operation_id="cancelLegalChange",
    response_description="Отмененное событие изменения.",
)
async def cancel_legal_change(
    change_id: Annotated[int, Path(ge=1, description="ID события изменения.")],
    service: LegalChangesServiceDep,
    _: VerifyApiKeyDep,
    review_notes: Annotated[str | None, Query(description="Причина отмены.")] = None,
) -> LegalChangeSchema:
    """Отменяет событие изменения."""

    logger.info("🚀 Запрос POST /legal-changes/%s/cancel.", change_id)
    try:
        result = await service.cancel_change(change_id=change_id, review_notes=review_notes)
        logger.info("✅ Событие отменено. id=%s", result.id)
        return result
    except (LegalChangeNotFoundError, LegalChangeServiceError) as error:
        logger.exception("❌ Ошибка отмены события: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
