from pydantic import BaseModel, Field


class HealthSchema(BaseModel):
    """Ответ healthcheck."""

    status: str = Field(..., description="Состояние приложения.", examples=["ok"])
    database: str = Field(..., description="Состояние подключения к PostgreSQL.", examples=["ok"])
