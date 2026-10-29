"""One retrieved source the script is allowed to use."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    text: str
    url: str
    license: str
    author: str
    retrieved_at: datetime
    image_url: str | None = None

    @field_validator("title", "text", "license", "author")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("url must be http or https")
        return cleaned

    @field_validator("image_url")
    @classmethod
    def optional_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("image_url must be http or https")
        return cleaned

    @field_validator("retrieved_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        return value
