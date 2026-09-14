from pydantic import BaseModel, ConfigDict, Field


class RedactionSection(BaseModel):
    """Статья в границах конкретной редакции документа."""

    model_config = ConfigDict(frozen=True)

    number: str = Field(
        ...,
        description="Номер статьи; надстрочный индекс приводится к виду «60.1».",
        examples=["59", "60.1", "351.8"],
    )
    title: str = Field(
        ..., description="Название статьи без её номера.", examples=["Срочный трудовой договор"],
    )
    first_paragraph_position: int = Field(
        ...,
        description="Позиция первого абзаца статьи в редакции по официальному оглавлению.",
        examples=[733],
    )
    last_paragraph_position: int = Field(
        ...,
        description="Позиция последнего абзаца статьи в редакции по официальному оглавлению.",
        examples=[788],
    )
