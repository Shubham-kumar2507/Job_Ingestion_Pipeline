from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class JobListing(BaseModel):
    """Normalized job record.

    Required fields (source, external_id, title, company, url) intentionally
    reject bad source data with a loud validation error rather than silently
    accepting blank or missing values.  Optional enrichment fields degrade
    gracefully to ``None`` so partial source records are still stored.
    """

    model_config = ConfigDict(extra="ignore")

    source: str
    external_id: str
    title: str
    company: str
    url: HttpUrl
    description: str | None = None
    location: str | None = None
    employment_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    extracted_skills: list[str] = Field(default_factory=list)
    seniority: str | None = None
    work_model: str | None = None
    source_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", "external_id", "title", "company")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value
