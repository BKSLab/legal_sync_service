from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.db.models.legal_changes import LegalChangeStatus


class ProcessingPreviewRequest(BaseModel):
    """Условное время только для пробного запуска одного события."""

    as_of: AwareDatetime = Field(
        description="Условный момент проверки с часовым поясом; часы сервера не меняются.",
        examples=["2027-09-01T00:00:00Z"],
    )


class ProcessingPreviewResult(BaseModel):
    """Результат загрузки и извлечения, без подтверждения и отправки события."""

    change_id: int
    as_of: datetime
    send_at: datetime
    event_status: LegalChangeStatus
    section_number: str
    redaction_id: int | None
    redaction_date: date | None
    outcome: Literal["not_due", "redaction_not_ready", "extracted"]
    message: str
    consolidated_text: str | None = None
    consolidated_text_source: str | None = None
    text_length: int = 0
    rag_sent: Literal[False] = False
