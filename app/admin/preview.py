import logging
import secrets
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError
from sqladmin import BaseView, expose
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.clients.pravo_ebpi import PravoEbpiClient
from app.core.settings import get_settings
from app.exceptions.legal_changes import (
    LegalChangeNotFoundError,
    LegalChangePreviewConflictError,
    LegalChangeRepositoryError,
)
from app.exceptions.pravo_ebpi import PravoEbpiClientError
from app.exceptions.redaction import RedactionParseError, RedactionSectionNotFoundError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.processing_preview import ProcessingPreviewRequest
from app.services.processing_preview import ProcessingPreviewService
from app.services.section_text import SectionTextService

logger = logging.getLogger(__name__)


class ChangePreviewView(BaseView):
    name = "Пробное извлечение статьи"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/legal-change/preview/{change_id:int}", methods=["GET", "POST"])
    async def preview_change(self, request: Request):
        form = await request.form() if request.method == "POST" else None
        token = request.session.get("preview_csrf_token", "")
        if form is not None:
            submitted = form.get("csrf_token", "")
            if not token or not isinstance(submitted, str) or not secrets.compare_digest(token, submitted):
                raise HTTPException(403, "Обновите страницу проверки и повторите запрос.")
        if not token:
            token = secrets.token_urlsafe(32)
            request.session["preview_csrf_token"] = token

        async with self._admin_ref.session_maker() as session:
            repository = LegalChangesRepository(session)
            change = await repository.get_by_id(request.path_params["change_id"])
            if change is None:
                raise HTTPException(404, "Событие не найдено.")
            # SQLAdmin истекает ORM-объекты после commit. Шаблону передаём
            # готовые значения, чтобы он не запускал ленивые запросы к БД.
            change_view = {
                "id": change.id, "section_number": change.section_number,
                "status": change.status,
                "tracked_document": {"short_name": change.tracked_document.short_name},
            }
            default_time = change.send_at or datetime.now(UTC)
            as_of = default_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
            result, error, status_code = None, None, 200
            if form is not None:
                as_of = str(form.get("as_of", ""))
                try:
                    data = ProcessingPreviewRequest(as_of=f"{as_of}Z")
                    async with httpx.AsyncClient() as client:
                        service = ProcessingPreviewService(
                            repository,
                            SectionTextService(PravoEbpiClient(client, get_settings().pravo_ebpi)),
                        )
                        result = await service.preview_change(change.id, data)
                except ValidationError:
                    error, status_code = "Укажите корректные дату и время в UTC.", 422
                except (
                    LegalChangeNotFoundError, LegalChangePreviewConflictError,
                    PravoEbpiClientError, RedactionParseError, RedactionSectionNotFoundError,
                ) as exc:
                    error, status_code = exc.detail, exc.status_code
                    logger.warning("Пробное извлечение в админке: %s", exc)
                except LegalChangeRepositoryError:
                    logger.exception("Не удалось сохранить пробное извлечение в админке.")
                    error, status_code = "Не удалось сохранить результат проверки.", 500
            return await self.templates.TemplateResponse(
                request, "processing_preview.html", {
                    "title": "Пробное извлечение статьи", "change": change_view,
                    "as_of": as_of, "csrf_token": token, "result": result, "error": error,
                }, status_code=status_code,
            )
