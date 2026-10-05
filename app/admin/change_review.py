import logging
import secrets
from datetime import UTC, datetime

import httpx
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqladmin import BaseView, expose
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.clients.pravo_ebpi import PravoEbpiClient
from app.core.settings import get_settings
from app.db.models.legal_changes import LegalChangeStatus
from app.exceptions.legal_changes import (
    LegalChangeInvalidStatusError,
    LegalChangeNotFoundError,
    LegalChangeRepositoryError,
    LegalChangeReviewConflictError,
)
from app.exceptions.pravo_ebpi import PravoEbpiClientError
from app.exceptions.redaction import (
    RedactionNotReadyError,
    RedactionParseError,
    RedactionSectionNotFoundError,
)
from app.repositories.configuration import ConfigurationRepository
from app.repositories.legal_changes import LegalChangesRepository
from app.repositories.tracked_documents import TrackedDocumentsRepository
from app.schemas.legal_changes import LegalChangeReviewRequest
from app.services.change_review import compare_article, official_document_url
from app.services.configuration import initial_configuration
from app.services.legal_changes import LegalChangesService
from app.services.section_text import SectionTextService

logger = logging.getLogger(__name__)


async def render_change_details(admin, request, model, *, error=None, values=None, status_code=200):
    settings = get_settings()
    token = request.session.get("change_review_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["change_review_csrf_token"] = token
    configuration, configuration_error, next_delivery = None, None, None
    try:
        async with admin.session_maker() as session:
            configuration = await ConfigurationRepository(session).get_or_create(initial_configuration(settings))
        if settings.scheduler.scheduler_enabled and configuration.rag_delivery_enabled and model.send_at:
            next_delivery = CronTrigger(
                hour=configuration.processing_cron_hour, minute=0, timezone=configuration.timezone,
            ).get_next_fire_time(None, max(datetime.now(UTC), model.send_at))
    except (SQLAlchemyError, OSError):
        logger.exception("Не удалось прочитать расписание отправки для карточки события.")
        configuration_error = "Расписание отправки сейчас недоступно."
    comparison, comparison_error = None, None
    if request.query_params.get("compare") == "1":
        try:
            async with httpx.AsyncClient() as client:
                comparison = await compare_article(model, SectionTextService(PravoEbpiClient(client, settings.pravo_ebpi)))
        except (PravoEbpiClientError, RedactionNotReadyError, RedactionParseError, RedactionSectionNotFoundError) as exc:
            logger.warning("Сравнение события №%s недоступно: %s", model.id, exc)
            comparison_error = str(exc)
    model_view = admin._find_model_view("legal-change")
    response = await admin.templates.TemplateResponse(request, "legal_change_details.html", {
        "model": model, "model_view": model_view, "title": f"Проверка изменения · событие №{model.id}",
        "csrf_token": token, "review_error": error, "review_values": values or {},
        "configuration": configuration, "configuration_error": configuration_error,
        "scheduler_enabled": settings.scheduler.scheduler_enabled, "next_delivery": next_delivery,
        "comparison": comparison, "comparison_error": comparison_error,
        "amendment_url": official_document_url(model.amending_doc_hash, model.amending_law_ref),
        "document_url": official_document_url(model.tracked_document.ebpi_doc_hash),
    }, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


class ChangeReviewView(BaseView):
    name = "Решение по изменению"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/legal-change/review/{change_id:int}", methods=["POST"])
    async def review_change(self, request: Request):
        form = await request.form()
        token = request.session.get("change_review_csrf_token", "")
        submitted = str(form.get("csrf_token", ""))
        if not token or not secrets.compare_digest(token.encode(), submitted.encode()):
            raise HTTPException(403, "Обновите карточку и повторите действие.")
        decision = form.get("decision")
        if decision not in ("approve", "reject"):
            raise HTTPException(400, "Выберите подтверждение или отклонение события.")
        async with self._admin_ref.session_maker() as session:
            repository = LegalChangesRepository(session)
            change = await repository.get_by_id(request.path_params["change_id"])
            if change is None:
                raise HTTPException(404, "Событие не найдено.")
            error, status_code = None, 422
            if change.status != LegalChangeStatus.DRAFT or str(form.get("updated_at", "")) != change.updated_at.isoformat():
                error, status_code = "Событие уже изменилось. Обновите карточку перед принятием решения.", 409
            elif decision == "approve" and not form.get("effective_date"):
                error = "Укажите дату вступления изменения в силу."
            else:
                try:
                    data = LegalChangeReviewRequest(
                        reviewed_by=get_settings().admin.admin_login.get_secret_value(),
                        review_notes=str(form.get("review_notes", "")).strip() or None,
                        effective_date=form.get("effective_date") if decision == "approve" else None,
                    )
                    service = LegalChangesService(repository, TrackedDocumentsRepository(session))
                    if decision == "approve":
                        await service.approve_change(change.id, data)
                    else:
                        await service.reject_change(change.id, data)
                except ValidationError:
                    error = "Проверьте дату вступления изменения в силу."
                except (LegalChangeReviewConflictError, LegalChangeInvalidStatusError):
                    error, status_code = "Событие уже изменилось. Обновите карточку перед принятием решения.", 409
                except LegalChangeNotFoundError:
                    raise HTTPException(404, "Событие не найдено.") from None
                except LegalChangeRepositoryError:
                    logger.exception("Не удалось сохранить решение по событию.")
                    error, status_code = "Решение не сохранено. Повторите попытку позже.", 503
                else:
                    return RedirectResponse(
                        request.url_for("admin:details", identity="legal-change", pk=change.id).include_query_params(review=decision),
                        status_code=303,
                    )
            # После rollback ORM-поля могут истечь; заново читаем карточку.
            change = await repository.get_by_id(request.path_params["change_id"])
            if change is None:
                raise HTTPException(404, "Событие не найдено.")
            return await render_change_details(
                self._admin_ref, request, change, error=error, values=dict(form), status_code=status_code,
            )
