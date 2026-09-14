from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.legal_changes import LegalChangeStatus


class LegalChangeCreateRequest(BaseModel):
    """Тело запроса создания события изменения."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tracked_document_id": 1,
                "section_number": "59",
                "section_title": "Срочный трудовой договор",
                "amending_law_ref": "от 26.07.2026 № 246-ФЗ",
                "amending_doc_hash": "797105206ac17902a9990082c79054b4f646947a5415d712d44ff90bf4ce8dd0",
                "ebpi_redaction_id": 495396,
                "redaction_date": "2027-03-01",
                "amending_act_type": "Федеральный закон",
                "amending_act_number": "246-ФЗ",
                "amending_act_date": "2026-07-26",
                "effective_date": "2027-03-01",
                "change_description": "199. на 01.03.2027 (№ 246-ФЗ от 26.07.2026)",
            }
        }
    )

    tracked_document_id: int = Field(
        ..., description="Внутренний ID отслеживаемого документа.", examples=[1],
    )
    section_number: str = Field(
        ..., min_length=1, max_length=100,
        description="Номер изменяемой статьи.", examples=["59", "60.1"],
    )
    section_title: str | None = Field(
        None, description="Заголовок изменяемой статьи.", examples=["Срочный трудовой договор"],
    )
    amending_law_ref: str = Field(
        ..., min_length=1, max_length=300,
        description="Реквизиты акта-поправки в виде ссылки на него.",
        examples=["от 26.07.2026 № 246-ФЗ"],
    )
    amending_doc_hash: str | None = Field(
        None, max_length=64,
        description="Идентификатор акта-поправки в банке редакций.",
        examples=["797105206ac17902a9990082c79054b4f646947a5415d712d44ff90bf4ce8dd0"],
    )
    ebpi_redaction_id: int | None = Field(
        None, description="Идентификатор редакции документа в банке редакций.", examples=[495396],
    )
    redaction_date: date | None = Field(
        None,
        description="Дата вступления редакции в силу по данным портала.",
        examples=["2027-03-01"],
    )
    amending_act_type: str | None = Field(
        None, max_length=120, description="Вид акта-поправки.", examples=["Федеральный закон"],
    )
    amending_act_number: str | None = Field(
        None, max_length=100, description="Номер акта-поправки.", examples=["246-ФЗ"],
    )
    amending_act_date: date | None = Field(
        None, description="Дата подписания акта-поправки.", examples=["2026-07-26"],
    )
    adoption_date: date | None = Field(
        None, description="Дата подписания закона-поправки.", examples=["2026-07-26"],
    )
    effective_date: date | None = Field(
        None, description="Дата вступления изменений в силу.", examples=["2027-03-01"],
    )
    change_description: str | None = Field(
        None,
        description="Краткое описание изменения; для автоматических событий — подпись редакции.",
        examples=["199. на 01.03.2027 (№ 246-ФЗ от 26.07.2026)"],
    )
    delta_text: str | None = Field(
        None,
        description="Фрагмент текста акта-поправки.",
        examples=["часть 4 статьи 17 после цифр \"8 - 10\" дополнить словами \"части 1\""],
    )


class LegalChangeReviewRequest(BaseModel):
    """Тело запроса ручного подтверждения события."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reviewed_by": "ivanov",
                "review_notes": "Проверено по тексту редакции.",
                "effective_date": "2027-03-01",
            }
        }
    )

    reviewed_by: str = Field(
        ..., min_length=1, max_length=200,
        description="Пользователь, подтвердивший событие.", examples=["ivanov"],
    )
    review_notes: str | None = Field(
        None, description="Примечания оператора.", examples=["Проверено по тексту редакции."],
    )
    effective_date: date | None = Field(
        None,
        description="Уточнённая дата вступления в силу; по умолчанию берётся из события.",
        examples=["2027-03-01"],
    )


class LegalChangeSchema(BaseModel):
    """Схема события изменения в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Внутренний ID события.", examples=[10])
    tracked_document_id: int = Field(..., description="Внутренний ID отслеживаемого документа.", examples=[1])
    section_number: str = Field(..., description="Номер изменяемой статьи.", examples=["59"])
    section_title: str | None = Field(
        None, description="Заголовок изменяемой статьи.", examples=["Срочный трудовой договор"],
    )
    amending_law_ref: str = Field(
        ..., description="Реквизиты акта-поправки.", examples=["от 26.07.2026 № 246-ФЗ"],
    )
    amending_doc_hash: str | None = Field(
        None,
        description="Идентификатор акта-поправки в банке редакций.",
        examples=["797105206ac17902a9990082c79054b4f646947a5415d712d44ff90bf4ce8dd0"],
    )
    ebpi_redaction_id: int | None = Field(
        None, description="Идентификатор редакции документа.", examples=[495396],
    )
    redaction_date: date | None = Field(
        None, description="Дата вступления редакции в силу.", examples=["2027-03-01"],
    )
    amending_act_type: str | None = Field(
        None, description="Вид акта-поправки.", examples=["Федеральный закон"],
    )
    amending_act_number: str | None = Field(None, description="Номер акта-поправки.", examples=["246-ФЗ"])
    amending_act_date: date | None = Field(
        None, description="Дата подписания акта-поправки.", examples=["2026-07-26"],
    )
    adoption_date: date | None = Field(
        None, description="Дата подписания закона-поправки.", examples=["2026-07-26"],
    )
    effective_date: date | None = Field(
        None, description="Дата вступления изменений в силу.", examples=["2027-03-01"],
    )
    change_description: str | None = Field(
        None,
        description="Краткое описание изменения.",
        examples=["199. на 01.03.2027 (№ 246-ФЗ от 26.07.2026)"],
    )
    delta_text: str | None = Field(None, description="Фрагмент текста акта-поправки.", examples=[None])
    consolidated_text: str | None = Field(
        None,
        description="Текст статьи из редакции, отправленный в RAG Service.",
        examples=["Статья 59. Срочный трудовой договор\nСрочный трудовой договор заключается: ..."],
    )
    consolidated_text_source: str | None = Field(
        None,
        description="Источник консолидированного текста.",
        examples=["actual.pravo.gov.ru: редакция 495396 от 2027-03-01"],
    )
    audience_override: str | None = Field(
        None, description="Переопределение аудитории для RAG Service.", examples=["employer"],
    )
    topics_override: list[str] | None = Field(
        None, description="Переопределение тем для RAG Service.", examples=[["охрана труда"]],
    )
    source_title_override: str | None = Field(
        None, description="Переопределение наименования источника для RAG Service.", examples=[None],
    )
    status: LegalChangeStatus = Field(..., description="Текущий статус события.", examples=["scheduled"])
    send_at: datetime | None = Field(
        None,
        description="Запланированный момент отправки в RAG Service.",
        examples=["2027-03-01T00:00:00Z"],
    )
    reviewed_by: str | None = Field(None, description="Кто подтвердил событие.", examples=["ivanov"])
    reviewed_at: datetime | None = Field(
        None, description="Когда событие подтверждено.", examples=["2026-09-07T10:15:00Z"],
    )
    review_notes: str | None = Field(
        None, description="Примечания оператора.", examples=["Проверено по тексту редакции."],
    )
    sent_at: datetime | None = Field(
        None, description="Фактический момент отправки в RAG Service.", examples=["2027-03-01T04:00:12Z"],
    )
    rag_response: dict | None = Field(
        None,
        description="Ответ RAG Service на обновление статьи.",
        examples=[{"document_id": "tk-197-2001", "section_number": "59", "chunks_count": 4}],
    )
    retry_count: int = Field(..., description="Количество выполненных попыток отправки.", examples=[0])
    last_error: str | None = Field(
        None, description="Причина последней неудачной обработки.", examples=[None],
    )
    created_at: datetime = Field(..., description="Момент создания записи.", examples=["2026-09-07T03:00:00Z"])
    updated_at: datetime = Field(..., description="Момент последнего изменения записи.", examples=["2026-09-07T03:00:00Z"])


class LegalChangesListSchema(BaseModel):
    """Пагинированный список событий изменений."""

    total: int = Field(..., description="Общее количество записей, удовлетворяющих фильтрам.", examples=[42])
    page: int = Field(..., description="Текущий номер страницы.", examples=[1])
    page_size: int = Field(..., description="Количество записей на странице.", examples=[20])
    items: list[LegalChangeSchema] = Field(..., description="События изменений на текущей странице.")
