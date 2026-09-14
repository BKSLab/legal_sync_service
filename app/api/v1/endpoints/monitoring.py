import logging

from fastapi import APIRouter, HTTPException, status

from app.dependencies.auth import VerifyApiKeyDep
from app.dependencies.services import MonitoringServiceDep, ProcessingServiceDep
from app.exceptions.pravo_ebpi import PravoEbpiClientError
from app.exceptions.tracked_documents import TrackedDocumentServiceError
from app.schemas.monitoring import MonitoringResult, ProcessingResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.post(
    path="/run",
    status_code=status.HTTP_200_OK,
    response_model=MonitoringResult,
    summary="Запустить мониторинг изменений вручную",
    description=(
        "Проверяет все документы на контроле и создаёт события изменений по новым "
        "редакциям. Идемпотентен: повторный запуск не создаёт дубликаты событий. "
        "Ту же операцию ежедневно выполняет планировщик."
    ),
    operation_id="runMonitoring",
    response_description="Сводка по каждому проверенному документу.",
    responses={
        200: {
            "description": "Мониторинг выполнен.",
            "content": {
                "application/json": {
                    "example": {
                        "documents_checked": 1,
                        "changes_created": 3,
                        "documents_failed": 0,
                        "items": [
                            {
                                "document_id": "labor_code_rf",
                                "redactions_total": 199,
                                "redactions_pending": 1,
                                "redactions_skipped_incomplete": 0,
                                "changes_created": 3,
                                "error": None,
                            }
                        ],
                    }
                }
            },
        },
        502: {"description": "Банк консолидированных редакций недоступен."},
    },
)
async def run_monitoring(
    service: MonitoringServiceDep,
    _: VerifyApiKeyDep,
) -> MonitoringResult:
    """Запускает мониторинг изменений отслеживаемых документов.

    Args:
        service: Сервис мониторинга.
        _: Проверка API-ключа.

    Returns:
        Сводка запуска мониторинга.
    """

    logger.info("🚀 Запрос POST /monitoring/run.")
    try:
        result = await service.run_monitoring()
    except (PravoEbpiClientError, TrackedDocumentServiceError) as error:
        logger.exception("❌ Ошибка мониторинга: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    logger.info("✅ Запрос POST /monitoring/run выполнен. событий=%s", result.changes_created)
    return result


@router.post(
    path="/process",
    status_code=status.HTTP_200_OK,
    response_model=ProcessingResult,
    summary="Запустить отправку изменений в RAG вручную",
    description=(
        "Отправляет в RAG Service подтверждённые события, у которых наступила дата "
        "вступления в силу. Ту же операцию ежедневно выполняет планировщик."
    ),
    operation_id="runProcessing",
    response_description="Сводка запуска обработки очереди.",
    responses={
        200: {
            "description": "Очередь обработана.",
            "content": {
                "application/json": {
                    "example": {
                        "changes_selected": 3,
                        "changes_sent": 3,
                        "changes_failed": 0,
                        "changes_postponed": 0,
                    }
                }
            },
        },
    },
)
async def run_processing(
    service: ProcessingServiceDep,
    _: VerifyApiKeyDep,
) -> ProcessingResult:
    """Запускает отправку готовых событий в RAG Service.

    Args:
        service: Сервис обработки очереди.
        _: Проверка API-ключа.

    Returns:
        Сводка запуска обработки.
    """

    logger.info("🚀 Запрос POST /monitoring/process.")
    result = await service.run_processing()
    logger.info("✅ Запрос POST /monitoring/process выполнен. отправлено=%s", result.changes_sent)
    return result
