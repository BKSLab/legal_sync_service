import secrets
from datetime import UTC, datetime

from sqladmin import BaseView, expose
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from app.core.settings import VerificationSettings, get_settings
from app.db.models import AutomationCase, AutomationRun, LegalChange, TrackedDocument
from app.repositories.automation import AutomationRepository
from app.repositories.configuration import ConfigurationRepository
from app.services.change_review import official_document_url
from app.services.configuration import initial_configuration

AUTOMATION_STATUSES = {
    "pending": "Ожидает автопроверки", "running": "Проверка выполняется", "approved": "Подтверждено автоматически",
    "needs_review": "Нужен оператор", "error": "Ошибка · ожидает повтора", "shadow_pass": "Проверено без отправки",
    "interrupted": "Проверка прервана",
    "resolved": "Разобрано оператором", "delivered": "Доставлено в RAG",
}
AUTOMATION_STAGES = {"queued": "В очереди", "sources": "Официальные источники", "comparison": "Сравнение всех статей",
                     "llm": "Проверка закона через LLM", "decision": "Решение", "delivery": "Приёмка RAG"}


async def automation_summary(session):
    counts = dict((await session.execute(select(AutomationCase.status, func.count()).group_by(AutomationCase.status))).all())
    last_run = await session.scalar(select(AutomationRun).order_by(AutomationRun.id.desc()).limit(1))
    if last_run:
        session.expunge(last_run)
    return {"counts": counts, "last_run": last_run}


class AutomationView(BaseView):
    name = "Автоматизация"
    icon = "fa-solid fa-gears"

    @expose("/automation", methods=["GET"])
    async def automation(self, request):
        settings = get_settings()
        status = request.query_params.get("status", "")
        if status and status not in AUTOMATION_STATUSES:
            raise HTTPException(400, "Неизвестный фильтр.")
        try:
            page = max(1, int(request.query_params.get("page", "1")))
        except ValueError:
            page = 1
        async with self._admin_ref.session_maker() as session:
            configuration = await ConfigurationRepository(session).get_or_create(initial_configuration(settings))
            overview = await automation_summary(session)
            query = select(AutomationCase, TrackedDocument).join(TrackedDocument, TrackedDocument.id == AutomationCase.tracked_document_id)
            if status:
                query = query.where(AutomationCase.status == status)
            rows = (await session.execute(query.order_by(AutomationCase.updated_at.desc(), AutomationCase.id.desc()).offset((page - 1) * 30).limit(31))).all()
            items = []
            for case, document in rows[:30]:
                changes = list(await session.scalars(select(LegalChange).where(
                    LegalChange.tracked_document_id == case.tracked_document_id, LegalChange.ebpi_redaction_id == case.redaction_id,
                ).order_by(LegalChange.id)))
                items.append({"case": case, "document": document, "changes": changes,
                              "sent": sum(c.status.value == "sent" for c in changes),
                              "law": changes[0].amending_law_ref if changes else "Реквизиты требуют проверки"})
        verification = getattr(settings, "verification", VerificationSettings(_env_file=None))
        return await self.templates.TemplateResponse(request, "automation.html", {
            "title": "Автоматическое обновление RAG", "configuration": configuration, "overview": overview,
            "items": items, "status": status, "statuses": AUTOMATION_STATUSES, "stages": AUTOMATION_STAGES,
            "page": page, "has_next": len(rows) > 30, "llm_configured": bool(verification.verification_api_key),
            "scheduler_enabled": settings.scheduler.scheduler_enabled,
        })

    @expose("/automation/{case_id:int}", methods=["GET"])
    async def automation_case(self, request):
        token = request.session.get("automation_csrf_token") or secrets.token_urlsafe(32)
        request.session["automation_csrf_token"] = token
        async with self._admin_ref.session_maker() as session:
            case = await session.get(AutomationCase, request.path_params["case_id"])
            if case is None:
                raise HTTPException(404, "Проверка не найдена.")
            document = await session.get(TrackedDocument, case.tracked_document_id)
            changes = list(await session.scalars(select(LegalChange).where(
                LegalChange.tracked_document_id == case.tracked_document_id, LegalChange.ebpi_redaction_id == case.redaction_id,
            ).order_by(LegalChange.id)))
            runs = list(await session.scalars(select(AutomationRun).where(AutomationRun.case_id == case.id).order_by(AutomationRun.id.desc()).limit(20)))
        response = await self.templates.TemplateResponse(request, "automation_case.html", {
            "title": f"Автопроверка №{case.id}", "case": case, "document": document, "changes": changes,
            "runs": runs, "latest": runs[0] if runs else None, "statuses": AUTOMATION_STATUSES,
            "stages": AUTOMATION_STAGES, "csrf_token": token, "now": datetime.now(UTC),
            "law_url": official_document_url(case.amending_doc_hash),
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    @expose("/automation/{case_id:int}/retry", methods=["POST"])
    async def automation_retry(self, request):
        form = await request.form()
        token = request.session.get("automation_csrf_token", "")
        if not token or not secrets.compare_digest(token.encode(), str(form.get("csrf_token", "")).encode()):
            raise HTTPException(403, "Обновите страницу перед повторной проверкой.")
        async with self._admin_ref.session_maker() as session:
            factory = async_sessionmaker(session.bind, expire_on_commit=False)
            if not await AutomationRepository(factory).retry(request.path_params["case_id"]):
                raise HTTPException(409, "Проверка уже выполняется или удалена.")
        return RedirectResponse(request.url_for("admin:automation_case", case_id=request.path_params["case_id"]), status_code=303)
