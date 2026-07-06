from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.legal_changes import LegalChangeStatus


class LegalChangeCreateRequest(BaseModel):
    """Тело запроса создания события изменения."""

    tracked_document_id: int = Field(..., description="ID отслеживаемого документа.")
    section_number: str = Field(..., min_length=1, max_length=100, description="Номер статьи.")
    section_title: str | None = Field(None, description="Заголовок статьи.")
    amending_law_ref: str = Field(..., min_length=1, max_length=300, description="eoNumber или URL.")
    adoption_date: date | None = Field(None, description="Дата подписания закона-поправки.")
    effective_date: date | None = Field(None, description="Дата вступления в силу.")
    change_description: str | None = Field(None, description="Описание изменения.")
    delta_text: str | None = Field(None, description="Фрагмент закона-поправки.")


class LegalChangeReviewRequest(BaseModel):
    """Тело запроса ручного подтверждения события."""

    reviewed_by: str = Field(..., min_length=1, max_length=200, description="Кто подтвердил событие.")
    review_notes: str | None = Field(None, description="Примечания оператора.")
    effective_date: date | None = Field(None, description="Уточненная дата вступления в силу.")


class LegalChangeSchema(BaseModel):
    """Схема события изменения в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Внутренний ID события.")
    tracked_document_id: int = Field(..., description="ID отслеживаемого документа.")
    section_number: str = Field(..., description="Номер статьи.")
    section_title: str | None = Field(None, description="Заголовок статьи.")
    amending_law_ref: str = Field(..., description="eoNumber или URL закона-поправки.")
    adoption_date: date | None = Field(None, description="Дата подписания.")
    effective_date: date | None = Field(None, description="Дата вступления в силу.")
    change_description: str | None = Field(None, description="Описание изменения.")
    delta_text: str | None = Field(None, description="Фрагмент поправки.")
    consolidated_text: str | None = Field(None, description="Актуальный текст статьи.")
    consolidated_text_source: str | None = Field(None, description="Источник актуального текста.")
    audience_override: str | None = Field(None, description="Переопределение аудитории.")
    topic_override: str | None = Field(None, description="Переопределение темы.")
    source_title_override: str | None = Field(None, description="Переопределение источника.")
    status: LegalChangeStatus = Field(..., description="Статус события.")
    send_at: datetime | None = Field(None, description="Запланированная отправка.")
    reviewed_by: str | None = Field(None, description="Кто подтвердил.")
    reviewed_at: datetime | None = Field(None, description="Когда подтверждено.")
    review_notes: str | None = Field(None, description="Примечания.")
    sent_at: datetime | None = Field(None, description="Дата отправки.")
    rag_response: dict | None = Field(None, description="Ответ RAG.")
    retry_count: int = Field(..., description="Количество попыток.")
    last_error: str | None = Field(None, description="Последняя ошибка.")
    created_at: datetime = Field(..., description="Дата создания.")
    updated_at: datetime = Field(..., description="Дата обновления.")


class LegalChangesListSchema(BaseModel):
    """Пагинированный список событий изменений."""

    total: int = Field(..., description="Общее количество записей.")
    page: int = Field(..., description="Номер страницы.")
    page_size: int = Field(..., description="Размер страницы.")
    items: list[LegalChangeSchema] = Field(..., description="События на странице.")
