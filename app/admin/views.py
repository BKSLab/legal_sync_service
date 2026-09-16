import json
from datetime import UTC, date, datetime

from markupsafe import Markup, escape
from sqladmin import BaseView, ModelView, expose
from starlette.requests import Request
from wtforms import SelectField

from app.admin.dashboard import get_dashboard_stats
from app.admin.forms import AdminForm, AdminJSONField, UTCDateTimeField
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
_CATEGORY_LABELS = {
    "labor_code": "Трудовой кодекс", "federal_law": "Федеральный закон", "other_npa": "Другой нормативный акт",
}
_AUDIENCE_LABELS = {"seeker": "Соискатели", "employer": "Работодатели", "both": "Все аудитории"}
_TYPE_FORMATTERS = {
    type(None): lambda value: "—",
    bool: lambda value: "Да" if value else "Нет",
    date: lambda value: value.strftime("%d.%m.%Y"),
    datetime: lambda value: value.astimezone(UTC).strftime("%d.%m.%Y %H:%M:%S")
    if value.tzinfo else value.strftime("%d.%m.%Y %H:%M:%S"),
}


def format_change_status(value: LegalChangeStatus | str) -> Markup:
    """Оформление исходов в той же палитре, что журнал изменений RAG."""

    color, label = _STATUS_STYLES.get(value, ("secondary", str(value)))
    return Markup(f'<span class="badge bg-{color}-lt">{escape(label)}</span>')


def _status_badge(model: LegalChange, attr: str) -> Markup:
    """Рендерит статус события как бейдж."""

    return format_change_status(getattr(model, attr))


def _format_text(model, attr: str) -> Markup:
    value = getattr(model, attr, None)
    if not value:
        return Markup("—")
    return Markup(f'<pre class="record-text">{escape(value)}</pre>')


def _format_json(model, attr: str) -> Markup:
    value = getattr(model, attr, None)
    if value is None:
        return Markup("—")
    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    return Markup(f'<pre class="record-text">{escape(pretty)}</pre>')


def _category_label(model, attr: str) -> str:
    value = getattr(model, attr)
    return _CATEGORY_LABELS.get(value, value or "—")


def _audience_label(model, attr: str) -> str:
    value = getattr(model, attr)
    return _AUDIENCE_LABELS.get(value, value or "—")


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
    column_labels = {
        "id": "ID", "document_id": "Идентификатор в RAG", "short_name": "Название документа",
        "full_title": "Полное наименование акта", "category": "Категория", "audience": "Аудитория",
        "topics": "Темы", "source_title": "Наименование источника", "publication_block": "Блок публикации",
        "document_number": "Номер акта", "adoption_date": "Дата подписания акта",
        "ebpi_doc_hash": "Ключ документа в банке редакций", "ebpi_doc_id": "ID в банке редакций",
        "monitor_from": "Начало контроля", "is_active": "На контроле", "legal_changes": "События изменений",
        "created_at": "Создано (UTC)", "updated_at": "Обновлено (UTC)",
    }
    column_type_formatters = _TYPE_FORMATTERS
    column_export_list = list(TrackedDocument.__table__.columns.keys())
    form_base_class = AdminForm
    form_excluded_columns = ["legal_changes", "created_at", "updated_at"]
    form_overrides = {"topics": AdminJSONField, "category": SelectField, "audience": SelectField}
    form_args = {
        "category": {"choices": list(_CATEGORY_LABELS.items())},
        "audience": {"choices": list(_AUDIENCE_LABELS.items())},
        "short_name": {"description": "Краткая подпись для реестра и карточек событий."},
        "full_title": {"description": "Наименование акта для поиска в банке консолидированных редакций."},
        "topics": {"description": 'Список тем в JSON, например []. Темы допустимы для категории «Другой нормативный акт».'},
        "monitor_from": {"default": date.today},
    }
    column_list = [
        TrackedDocument.id,
        TrackedDocument.short_name,
        TrackedDocument.document_number,
        TrackedDocument.adoption_date,
        TrackedDocument.category,
        TrackedDocument.monitor_from,
        TrackedDocument.is_active,
        TrackedDocument.updated_at,
    ]
    column_searchable_list = [
        TrackedDocument.document_id,
        TrackedDocument.short_name,
        TrackedDocument.full_title,
        TrackedDocument.document_number,
    ]
    column_sortable_list = [
        TrackedDocument.id,
        TrackedDocument.short_name,
        TrackedDocument.is_active,
        TrackedDocument.updated_at,
    ]
    column_default_sort = [(TrackedDocument.id, True)]
    column_formatters = {TrackedDocument.category: _category_label}
    column_formatters_detail = {
        TrackedDocument.topics: _format_json,
        TrackedDocument.category: _category_label,
        TrackedDocument.audience: _audience_label,
    }


class LegalChangeAdmin(ModelView, model=LegalChange):
    """Раздел админки для событий изменений."""

    name = "Событие"
    name_plural = "События изменений"
    icon = "fa-solid fa-clock-rotate-left"
    details_template = "legal_change_details.html"
    column_labels = {
        "id": "ID", "tracked_document": "Документ", "tracked_document_id": "ID документа",
        "section_number": "Статья", "section_title": "Название статьи", "amending_law_ref": "Акт-поправка",
        "amending_doc_hash": "Ключ акта-поправки в банке редакций", "ebpi_redaction_id": "ID редакции",
        "redaction_date": "Дата редакции", "effective_date": "Вступление в силу",
        "amending_act_type": "Вид акта-поправки", "amending_act_number": "Номер акта-поправки",
        "amending_act_date": "Дата подписания акта-поправки", "adoption_date": "Дата принятия закона-поправки",
        "change_description": "Описание изменения", "delta_text": "Фрагмент акта-поправки",
        "consolidated_text": "Извлечённый текст статьи", "consolidated_text_source": "Источник текста",
        "audience_override": "Уточнённая аудитория", "topics_override": "Уточнённые темы",
        "source_title_override": "Уточнённое наименование источника", "status": "Статус",
        "send_at": "Срок отправки (UTC)", "reviewed_by": "Кто проверил", "reviewed_at": "Проверено (UTC)",
        "review_notes": "Примечания проверяющего", "sent_at": "Отправлено (UTC)", "rag_response": "Ответ RAG",
        "retry_count": "Попытки отправки", "last_error": "Последняя ошибка",
        "created_at": "Создано (UTC)", "updated_at": "Обновлено (UTC)",
    }
    column_type_formatters = _TYPE_FORMATTERS
    column_export_list = list(LegalChange.__table__.columns.keys())
    form_base_class = AdminForm
    form_excluded_columns = ["created_at", "updated_at"]
    form_overrides = {
        "status": SelectField, "topics_override": AdminJSONField, "rag_response": AdminJSONField,
        "send_at": UTCDateTimeField, "reviewed_at": UTCDateTimeField, "sent_at": UTCDateTimeField,
    }
    form_args = {
        "status": {"choices": [(state.value, label) for state, (_, label) in _STATUS_STYLES.items()]},
        "consolidated_text": {"description": "Текст, полученный при пробном извлечении или отправке. Факт отправки указан отдельно."},
        "tracked_document": {"description": "Документ, статья которого изменилась."},
    }
    form_widget_args = {"consolidated_text": {"rows": 14}, "change_description": {"rows": 4}}
    column_list = [
        LegalChange.id,
        LegalChange.tracked_document,
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
    column_formatters = {
        LegalChange.status: _status_badge,
        LegalChange.tracked_document: lambda model, attr: model.tracked_document.short_name,
    }
    column_formatters_detail = {
        LegalChange.status: _status_badge,
        LegalChange.rag_response: _format_json,
        LegalChange.topics_override: _format_json,
        LegalChange.consolidated_text: _format_text,
        LegalChange.change_description: _format_text,
        LegalChange.last_error: _format_text,
        LegalChange.delta_text: _format_text,
        LegalChange.review_notes: _format_text,
        LegalChange.audience_override: _audience_label,
    }
