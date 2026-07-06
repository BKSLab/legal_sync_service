from markupsafe import Markup
from sqladmin import ModelView

from app.db.models.legal_changes import LegalChange
from app.db.models.tracked_documents import TrackedDocument


def _status_badge(model: LegalChange, attr: str) -> Markup:
    """Рендерит статус события как бейдж."""

    value = getattr(model, attr)
    return Markup(f"<span class='badge bg-secondary'>{value.value}</span>")


class TrackedDocumentAdmin(ModelView, model=TrackedDocument):
    """Раздел админки для реестра документов."""

    name = "Документ"
    name_plural = "Реестр документов"
    icon = "fa-solid fa-scale-balanced"
    column_list = [
        TrackedDocument.id,
        TrackedDocument.document_id,
        TrackedDocument.short_name,
        TrackedDocument.publication_block,
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


class LegalChangeAdmin(ModelView, model=LegalChange):
    """Раздел админки для событий изменений."""

    name = "Событие"
    name_plural = "События изменений"
    icon = "fa-solid fa-clock-rotate-left"
    column_list = [
        LegalChange.id,
        LegalChange.tracked_document_id,
        LegalChange.section_number,
        LegalChange.amending_law_ref,
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
    column_formatters_detail = {LegalChange.status: _status_badge}
