from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

TK_RF_DOC_HASH_EXAMPLE = "03bdee8f44a71247d7ba3342484326dbc3fa292d3caae9f88ada4b1ee38c20d9"


def _parse_portal_date(value: str | None) -> date | None:
    """Преобразует дату портала формата `YYYYMMDD` в `date`."""

    if not value:
        return None
    return date(year=int(value[0:4]), month=int(value[4:6]), day=int(value[6:8]))


class EbpiAdoption(BaseModel):
    """Реквизиты принятия акта — вид, номер и дата в разобранном виде."""

    model_config = ConfigDict(populate_by_name=True)

    act_type: str | None = Field(
        None, alias="type", description="Вид акта.", examples=["Федеральный закон"],
    )
    act_number: str | None = Field(
        None, alias="onumber", description="Номер акта.", examples=["246-ФЗ"],
    )
    act_date: date | None = Field(
        None, alias="odate", description="Дата подписания акта.", examples=["2026-07-26"],
    )
    organ: str | None = Field(
        None,
        alias="organ",
        description="Принявший орган; у федеральных законов портал оставляет поле пустым.",
        examples=[""],
    )

    @field_validator("act_date", mode="before")
    @classmethod
    def parse_act_date(cls, value: str | date | None) -> date | None:
        """Портал отдаёт дату принятия в формате `ДД.ММ.ГГГГ`."""

        if isinstance(value, str) and value:
            day, month, year = value.split(".")
            return date(year=int(year), month=int(month), day=int(day))
        return value or None


class EbpiDocumentCard(BaseModel):
    """Карточка правового акта в банке редакций."""

    model_config = ConfigDict(populate_by_name=True)

    doc_id: int = Field(
        ..., alias="docid", description="Внутренний идентификатор акта в банке.", examples=[64284],
    )
    doc_hash: str = Field(
        ...,
        alias="dochash",
        description="Стабильный глобальный идентификатор акта.",
        examples=[TK_RF_DOC_HASH_EXAMPLE],
    )
    doc_state: str | None = Field(
        None,
        alias="docstate",
        description="Состояние акта.",
        examples=["Действует с изменениями"],
    )
    doc_passing: str | None = Field(
        None,
        alias="docpassing",
        description="Вид акта с датой и номером.",
        examples=["Кодекс Российской Федерации от 30.12.2001 № 197-ФЗ"],
    )
    doc_name: str | None = Field(
        None,
        alias="docname",
        description="Наименование акта.",
        examples=["Трудовой кодекс Российской Федерации"],
    )
    adoptions: list[EbpiAdoption] = Field(
        default_factory=list,
        alias="adoptions",
        description="Реквизиты принятия акта в разобранном виде.",
    )

    @property
    def adoption(self) -> EbpiAdoption | None:
        """Основные реквизиты принятия акта."""

        return self.adoptions[0] if self.adoptions else None


class EbpiSearchResult(BaseModel):
    """Элемент выдачи поиска по реквизитам."""

    model_config = ConfigDict(populate_by_name=True)

    doc_id: int = Field(
        ..., alias="docid", description="Внутренний идентификатор акта в банке.", examples=[64284],
    )
    doc_hash: str = Field(
        ...,
        alias="dochash",
        description="Стабильный глобальный идентификатор акта.",
        examples=[TK_RF_DOC_HASH_EXAMPLE],
    )
    doc_names: str | None = Field(
        None,
        alias="docnames",
        description="Наименование акта.",
        examples=["Трудовой кодекс Российской Федерации"],
    )
    doc_passing: str | None = Field(
        None,
        alias="docpassing",
        description="Вид акта с датой и номером.",
        examples=["Кодекс Российской Федерации от 30.12.2001 № 197-ФЗ"],
    )
    doc_state: str | None = Field(
        None, alias="docstate", description="Состояние акта.", examples=["Действует с изменениями"],
    )
    doc_number: str | None = Field(
        None, alias="docpass0number", description="Номер акта.", examples=["197-ФЗ"],
    )
    adoption_date: date | None = Field(
        None, alias="docpass0date", description="Дата подписания акта.", examples=["2001-12-30"],
    )

    @field_validator("adoption_date", mode="before")
    @classmethod
    def parse_adoption_date(cls, value: str | date | None) -> date | None:
        """Портал отдаёт дату строкой `YYYYMMDD`, а не в ISO-формате."""

        if isinstance(value, str):
            return _parse_portal_date(value)
        return value


class EbpiRedaction(BaseModel):
    """Редакция правового акта.

    `redaction_date` — официальная дата вступления редакции в силу, поэтому
    выбор редакции на нужную дату не требует разбора текста акта-поправки.
    """

    model_config = ConfigDict(populate_by_name=True)

    redaction_id: int = Field(
        ..., alias="redid", description="Идентификатор редакции.", examples=[495396],
    )
    redaction_date: date = Field(
        ...,
        alias="reddate",
        description="Дата вступления редакции в силу.",
        examples=["2027-03-01"],
    )
    caption: str | None = Field(
        None,
        alias="redcaption",
        description="Подпись редакции с реквизитами акта-поправки.",
        examples=["199. на 01.03.2027 (№ 246-ФЗ от 26.07.2026), не вступившая в силу редакция"],
    )
    state_name: str | None = Field(
        None,
        alias="statename",
        description="Состояние акта в этой редакции.",
        examples=["Действует с изменениями"],
    )
    is_actual: bool = Field(
        False, alias="actual", description="Признак действующей сейчас редакции.", examples=[False],
    )
    is_completed: bool = Field(
        False,
        alias="redcompleted",
        description="Признак готовности текста: портал публикует редакцию раньше, чем её текст.",
        examples=[True],
    )
    has_content: bool = Field(
        False,
        alias="hascontent",
        description="Признак наличия оглавления редакции.",
        examples=[True],
    )
    is_initial: bool = Field(
        False,
        alias="redinitial",
        description="Признак исходной редакции акта.",
        examples=[False],
    )

    @field_validator("redaction_date", mode="before")
    @classmethod
    def parse_redaction_date(cls, value: str | date) -> date | None:
        """Портал отдаёт дату редакции строкой `YYYYMMDD`."""

        if isinstance(value, str):
            return _parse_portal_date(value)
        return value


class EbpiContentNode(BaseModel):
    """Узел структурного оглавления редакции.

    `first_paragraph_id`/`last_paragraph_id` — это id первого и последнего
    абзаца структурной единицы в HTML той же редакции. Благодаря им извлечение
    статьи выполняется срезом по официальному индексу, а не поиском по тексту.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(
        ..., alias="id", description="Идентификатор узла оглавления.", examples=["b1_si_h1_a1"],
    )
    caption: str = Field(
        "",
        alias="caption",
        description="Заголовок единицы; номер статьи может содержать надстрочный индекс.",
        examples=["Статья 60<sup>1</sup>. Работа по совместительству"],
    )
    unit: str = Field(
        "",
        alias="unit",
        description="Вид структурной единицы.",
        examples=["статья", "глава", "часть"],
    )
    level: int = Field(0, alias="lvl", description="Уровень вложенности узла.", examples=[3])
    first_paragraph_id: str | None = Field(
        None, alias="np", description="Id первого абзаца единицы.", examples=["p430"],
    )
    last_paragraph_id: str | None = Field(
        None, alias="npe", description="Id последнего абзаца единицы.", examples=["p6465"],
    )
