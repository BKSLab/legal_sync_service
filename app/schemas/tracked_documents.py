from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

# Категории RAG Service, для которых поддерживается гранулярное обновление статьи.
RAG_SECTION_CATEGORIES = ("labor_code", "federal_law", "other_npa")
# Категории RAG Service, для которых допустимы темы.
RAG_TOPICS_CATEGORIES = ("other_npa",)


class TrackedDocumentBase(BaseModel):
    """Базовые поля отслеживаемого документа."""
    rag_ingestion_id: str | None = Field(None, pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

    document_id: str = Field(
        ..., min_length=1, max_length=200,
        description="Идентификатор документа в RAG Service.", examples=["tk-197-2001"],
    )
    short_name: str = Field(
        ..., min_length=1, max_length=300,
        description="Краткая подпись документа в реестре.",
        examples=["Трудовой кодекс"],
    )
    full_title: str = Field(
        ..., min_length=1, description="Наименование акта для поиска в банке правовых актов.",
        examples=["Трудовой кодекс Российской Федерации"],
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=f"Категория для RAG. Допустимые значения: {list(RAG_SECTION_CATEGORIES)}.",
        examples=["labor_code"],
    )
    audience: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Аудитория для RAG: seeker, employer или both.",
        examples=["both"],
    )
    topics: list[str] = Field(
        default_factory=list,
        description=f"Темы для RAG. Допустимы только для категорий {list(RAG_TOPICS_CATEGORIES)}.",
        examples=[[]],
    )
    source_title: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Полное официальное наименование документа для ссылки на источник в RAG.",
        examples=['"Трудовой кодекс Российской Федерации" от 30.12.2001 № 197-ФЗ'],
    )
    publication_block: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Блок публикации на publication.pravo.gov.ru.",
        examples=["president"],
    )
    document_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Номер акта для поиска в банке консолидированных редакций.",
        examples=["197-ФЗ"],
    )
    adoption_date: date = Field(
        ...,
        description="Дата подписания акта.",
        examples=["2001-12-30"],
    )
    is_active: bool = Field(True, description="Флаг активности мониторинга.", examples=[True])


class TrackedDocumentCreateRequest(TrackedDocumentBase):
    """Тело запроса постановки документа на контроль."""

    monitor_from: date | None = Field(
        None,
        description="Дата, с которой отслеживаются редакции. По умолчанию — сегодня.",
        examples=["2026-09-07"],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "tk-197-2001",
                "short_name": "Трудовой кодекс",
                "full_title": "Трудовой кодекс Российской Федерации",
                "category": "labor_code",
                "audience": "both",
                "topics": [],
                "source_title": "\"Трудовой кодекс Российской Федерации\" от 30.12.2001 № 197-ФЗ",
                "publication_block": "president",
                "document_number": "197-ФЗ",
                "adoption_date": "2001-12-30",
                "monitor_from": "2026-09-07",
                "is_active": True,
            }
        }
    )


class TrackedDocumentUpdateRequest(BaseModel):
    """Тело запроса обновления отслеживаемого документа."""

    short_name: str | None = Field(
        None, min_length=1, max_length=300,
        description="Краткая подпись документа в реестре.", examples=["Трудовой кодекс"],
    )
    full_title: str | None = Field(
        None, min_length=1, description="Наименование акта для поиска в банке правовых актов.",
        examples=["Трудовой кодекс Российской Федерации"],
    )
    category: str | None = Field(
        None, min_length=1, max_length=200, description="Категория для RAG.", examples=["labor_code"],
    )
    audience: str | None = Field(
        None, min_length=1, max_length=200, description="Аудитория для RAG.", examples=["both"],
    )
    topics: list[str] | None = Field(None, description="Темы для RAG.", examples=[[]])
    source_title: str | None = Field(
        None, min_length=1, max_length=300,
        description="Наименование источника для RAG.",
        examples=['"Трудовой кодекс Российской Федерации" от 30.12.2001 № 197-ФЗ'],
    )
    publication_block: str | None = Field(
        None, min_length=1, max_length=100, description="Блок публикации.", examples=["president"],
    )
    document_number: str | None = Field(
        None, min_length=1, max_length=100, description="Номер акта.", examples=["197-ФЗ"],
    )
    adoption_date: date | None = Field(
        None, description="Дата подписания акта.", examples=["2001-12-30"],
    )
    monitor_from: date | None = Field(
        None, description="Дата, с которой отслеживаются редакции.", examples=["2026-09-07"],
    )
    is_active: bool | None = Field(
        None, description="Флаг активности мониторинга.", examples=[True],
    )


class TrackedDocumentSchema(TrackedDocumentBase):
    """Схема отслеживаемого документа в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Внутренний ID записи реестра.", examples=[1])
    monitor_from: date = Field(
        ..., description="Дата, с которой отслеживаются редакции.", examples=["2026-09-07"],
    )
    ebpi_doc_hash: str | None = Field(
        None,
        description="Идентификатор акта в банке редакций; определяется один раз.",
        examples=["03bdee8f44a71247d7ba3342484326dbc3fa292d3caae9f88ada4b1ee38c20d9"],
    )
    ebpi_doc_id: int | None = Field(
        None, description="Числовой идентификатор акта в банке редакций.", examples=[64284],
    )
    created_at: datetime = Field(
        ..., description="Момент создания записи.", examples=["2026-09-07T10:00:00Z"],
    )
    updated_at: datetime = Field(
        ..., description="Момент последнего изменения записи.", examples=["2026-09-07T10:00:00Z"],
    )


class TrackedDocumentsListSchema(BaseModel):
    """Пагинированный список отслеживаемых документов."""

    total: int = Field(
        ..., description="Общее количество записей, удовлетворяющих фильтрам.", examples=[3],
    )
    page: int = Field(..., description="Текущий номер страницы.", examples=[1])
    page_size: int = Field(..., description="Количество записей на странице.", examples=[20])
    items: list[TrackedDocumentSchema] = Field(
        ..., description="Отслеживаемые документы на текущей странице.",
    )
