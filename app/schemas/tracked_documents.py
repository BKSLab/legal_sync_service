from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TrackedDocumentBase(BaseModel):
    """Базовые поля отслеживаемого документа."""

    document_id: str = Field(..., min_length=1, max_length=200, description="ID документа в RAG.")
    short_name: str = Field(..., min_length=1, max_length=300, description="Краткое название.")
    full_title: str = Field(..., min_length=1, description="Полное наименование акта.")
    category: str = Field(..., min_length=1, max_length=200, description="Категория для RAG.")
    audience: str = Field(..., min_length=1, max_length=200, description="Аудитория для RAG.")
    topic: str = Field(..., min_length=1, max_length=200, description="Тема для RAG.")
    source_title: str = Field(..., min_length=1, max_length=300, description="Источник для RAG.")
    publication_block: str = Field(..., min_length=1, max_length=100, description="Блок публикации.")
    consolidated_source_url: str = Field(..., min_length=1, description="URL консолидированной редакции.")
    is_active: bool = Field(True, description="Флаг активности мониторинга.")


class TrackedDocumentCreateRequest(TrackedDocumentBase):
    """Тело запроса создания отслеживаемого документа."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "labor_code_rf",
                "short_name": "Трудовой кодекс",
                "full_title": "Трудовой кодекс Российской Федерации",
                "category": "law",
                "audience": "hr",
                "topic": "labor",
                "source_title": "Трудовой кодекс РФ",
                "publication_block": "president",
                "consolidated_source_url": "https://example.org/labor-code",
                "is_active": True,
            }
        }
    )


class TrackedDocumentUpdateRequest(BaseModel):
    """Тело запроса обновления отслеживаемого документа."""

    short_name: str | None = Field(None, min_length=1, max_length=300, description="Краткое название.")
    full_title: str | None = Field(None, min_length=1, description="Полное наименование акта.")
    category: str | None = Field(None, min_length=1, max_length=200, description="Категория для RAG.")
    audience: str | None = Field(None, min_length=1, max_length=200, description="Аудитория для RAG.")
    topic: str | None = Field(None, min_length=1, max_length=200, description="Тема для RAG.")
    source_title: str | None = Field(None, min_length=1, max_length=300, description="Источник для RAG.")
    publication_block: str | None = Field(None, min_length=1, max_length=100, description="Блок публикации.")
    consolidated_source_url: str | None = Field(None, min_length=1, description="URL редакции.")
    is_active: bool | None = Field(None, description="Флаг активности мониторинга.")


class TrackedDocumentSchema(TrackedDocumentBase):
    """Схема отслеживаемого документа в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Внутренний ID записи.")
    created_at: datetime = Field(..., description="Дата создания.")
    updated_at: datetime = Field(..., description="Дата обновления.")


class TrackedDocumentsListSchema(BaseModel):
    """Пагинированный список отслеживаемых документов."""

    total: int = Field(..., description="Общее количество записей.")
    page: int = Field(..., description="Номер страницы.")
    page_size: int = Field(..., description="Размер страницы.")
    items: list[TrackedDocumentSchema] = Field(..., description="Документы на странице.")
