import json

from markupsafe import Markup, escape
from sqladmin import BaseView, ModelView, expose
from starlette.requests import Request

from app.admin.dashboard import get_dashboard_stats
from app.core.settings import get_settings
from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.db.models.tracked_documents import TrackedDocument

_STATUS_STYLES = {
    LegalChangeStatus.DRAFT: ("orange", "Ожидает проверки"),
    LegalChangeStatus.APPROVED: ("cyan", "Подтверждено"),
    LegalChangeStatus.SCHEDULED: ("blue", "Запланировано"),
    LegalChangeStatus.PROCESSING: ("yellow", "Обрабатывается"),
    LegalChangeStatus.SENT: ("green", "Отправлено"),
    LegalChangeStatus.FAILED: ("red", "Ошибка"),
    LegalChangeStatus.CANCELLED: ("secondary", "Отменено"),
}
_TEXT_STYLE = "white-space:pre-wrap;word-break:break-word;max-width:900px;display:block;"


def format_change_status(value: LegalChangeStatus | str) -> Markup:
    """Оформление исходов в той же палитре, что журнал изменений RAG."""

    color, label = _STATUS_STYLES.get(value, ("secondary", str(value)))
    return Markup(f'<span class="badge bg-{color}-lt">{escape(label)}</span>')


def _status_badge(model: LegalChange, attr: str) -> Markup:
    """Рендерит статус события как бейдж."""

    return format_change_status(getattr(model, attr))


def _format_text(model, attr: str) -> Markup:
    value = getattr(model, attr, None)
    return Markup(f'<pre style="{_TEXT_STYLE}">{escape(value or "—")}</pre>')


def _format_json(model, attr: str) -> Markup:
    value = getattr(model, attr, None)
    pretty = json.dumps(value, ensure_ascii=False, indent=2) if value is not None else "—"
    return Markup(f'<pre style="{_TEXT_STYLE}">{escape(pretty)}</pre>')


class DashboardView(BaseView):
    name = "Дашборд"
    icon = "fa-solid fa-gauge-high"

    @expose("/dashboard", methods=["GET"])
    async def dashboard(self, request: Request):
        async with self._admin_ref.session_maker() as db_session:
            stats = await get_dashboard_stats(db_session)
        return await self.templates.TemplateResponse(
            request, "dashboard.html", {
                "stats": stats,
                "title": "Дашборд",
                "rag_delivery_enabled": get_settings().rag.rag_delivery_enabled,
            },
        )


class TrackedDocumentAdmin(ModelView, model=TrackedDocument):
    """Раздел админки для реестра документов."""

    name = "Документ"
    name_plural = "Реестр документов"
    icon = "fa-solid fa-scale-balanced"
    column_list = [
        TrackedDocument.id,
        TrackedDocument.document_id,
        TrackedDocument.short_name,
        TrackedDocument.document_number,
        TrackedDocument.category,
        TrackedDocument.ebpi_doc_hash,
        TrackedDocument.monitor_from,
        TrackedDocument.is_active,
        TrackedDocument.updated_at,
    ]
    column_searchable_list = [
        TrackedDocument.document_id,
        TrackedDocument.short_name,
        TrackedDocument.full_title,
    ]
    column_sortable_list = [
        TrackedDocument.id,
        TrackedDocument.short_name,
        TrackedDocument.is_active,
        TrackedDocument.updated_at,
    ]
    column_default_sort = [(TrackedDocument.id, True)]
    column_formatters_detail = {TrackedDocument.topics: _format_json}


class LegalChangeAdmin(ModelView, model=LegalChange):
    """Раздел админки для событий изменений."""

    name = "Событие"
    name_plural = "События изменений"
    icon = "fa-solid fa-clock-rotate-left"
    details_template = "legal_change_details.html"
    column_list = [
        LegalChange.id,
        LegalChange.tracked_document_id,
        LegalChange.section_number,
        LegalChange.amending_law_ref,
        LegalChange.redaction_date,
        LegalChange.effective_date,
        LegalChange.status,
        LegalChange.retry_count,
        LegalChange.updated_at,
    ]
    column_searchable_list = [
        LegalChange.section_number,
        LegalChange.amending_law_ref,
        LegalChange.change_description,
    ]
    column_sortable_list = [
        LegalChange.id,
        LegalChange.section_number,
        LegalChange.effective_date,
        LegalChange.status,
        LegalChange.retry_count,
        LegalChange.updated_at,
    ]
    column_default_sort = [(LegalChange.id, True)]
    column_formatters = {LegalChange.status: _status_badge}
    column_formatters_detail = {
        LegalChange.status: _status_badge,
        LegalChange.rag_response: _format_json,
        LegalChange.topics_override: _format_json,
        LegalChange.consolidated_text: _format_text,
        LegalChange.change_description: _format_text,
        LegalChange.last_error: _format_text,
    }
