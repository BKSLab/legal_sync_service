from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpectedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_number: str = Field(pattern=r"^\d+(?:\.\d+)*$")
    effective_date: date | None
    applies_to_revision: bool
    operation: Literal["changed", "added", "repealed"]
    law_quote: str = Field(min_length=10, max_length=8000)
    date_quote: str = Field(min_length=5, max_length=4000)
    assessment: Literal["matches", "mismatch", "unknown"]
    explanation: str = Field(min_length=1, max_length=2000)


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "needs_review"]
    summary: str = Field(min_length=1, max_length=4000)
    expected_articles: list[ExpectedArticle] = Field(max_length=500)
    unsupported_provisions: list[str] = Field(max_length=100)
    issues: list[str] = Field(max_length=100)
