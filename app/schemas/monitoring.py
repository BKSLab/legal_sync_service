from pydantic import BaseModel, Field


class MonitoringDocumentResult(BaseModel):
    """Итог проверки одного документа на контроле."""

    document_id: str = Field(
        ..., description="Идентификатор документа в RAG Service.", examples=["tk-197-2001"],
    )
    redactions_total: int = Field(
        0, description="Всего редакций у документа в банке правовых актов.", examples=[199],
    )
    redactions_pending: int = Field(
        0, description="Редакций, по которым события ещё не создавались.", examples=[1],
    )
    redactions_skipped_incomplete: int = Field(
        0,
        description="Редакций отложено: портал ещё готовит их текст.",
        examples=[0],
    )
    changes_created: int = Field(
        0, description="Создано новых событий изменений.", examples=[5],
    )
    error: str | None = Field(
        None,
        description="Причина отказа, если документ обработать не удалось.",
        examples=[None],
    )


class MonitoringResult(BaseModel):
    """Сводка одного запуска мониторинга."""

    run_id: int | None = Field(None, description="ID записи в журнале мониторинга.")
    already_running: bool = Field(False, description="Запуск пропущен: проверка уже выполняется или этот срок расписания уже обработан.")
    documents_checked: int = Field(..., description="Сколько документов проверено.", examples=[1])
    changes_created: int = Field(..., description="Сколько событий изменений создано.", examples=[5])
    documents_failed: int = Field(
        ..., description="По скольким документам произошёл отказ.", examples=[0],
    )
    items: list[MonitoringDocumentResult] = Field(
        ..., description="Итоги по каждому проверенному документу.",
    )


class ProcessingResult(BaseModel):
    """Сводка одного запуска обработки очереди отправки."""

    delivery_disabled: bool = Field(
        False, description="Отправка в RAG отключена в конфигурации сервиса.",
    )
    already_running: bool = Field(
        False, description="Очередь уже обрабатывается другим запуском.",
    )
    changes_recovered: int = Field(
        0, description="Сколько прерванных событий возвращено в очередь.",
    )
    changes_superseded: int = Field(
        0, description="Сколько событий отменено: в RAG уже есть более поздняя редакция.",
    )

    changes_selected: int = Field(
        ..., description="Сколько событий отобрано к отправке.", examples=[5],
    )
    changes_sent: int = Field(
        ..., description="Сколько событий отправлено в RAG Service.", examples=[5],
    )
    changes_failed: int = Field(
        ..., description="Сколько событий завершилось ошибкой.", examples=[0],
    )
    changes_postponed: int = Field(
        ...,
        description="Сколько событий отложено из-за неготового текста редакции.",
        examples=[0],
    )
