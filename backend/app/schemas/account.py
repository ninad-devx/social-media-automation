"""Account (brand) schemas.

Deliberately excludes anything Buffer-specific.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccountSummary(BaseModel):
    """Compact shape used by selectors and lists."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str
    logo: str | None = None
    color: str | None = None
    active: bool = True
    channel_count: int = 0


class AccountDetail(AccountSummary):
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    channels: list["ChannelDetail"] = Field(default_factory=list)


class AccountCreate(BaseModel):
    """Create a brand.

    The id is optional: a slug is derived from the name when it is omitted, so
    the UI does not have to invent stable identifiers.
    """

    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    type: str = Field(default="brand", max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    logo: str | None = Field(default=None, max_length=512)
    color: str | None = Field(default=None, max_length=16)
    sort_order: int | None = None

    @field_validator("name", "id")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        # min_length alone accepts "   ", which would produce an unnamed brand.
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    logo: str | None = Field(default=None, max_length=512)
    color: str | None = Field(default=None, max_length=16)
    active: bool | None = None
    sort_order: int | None = None

    @field_validator("name", "type")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


from app.schemas.channel import ChannelDetail  # noqa: E402  (circular-safe)

AccountDetail.model_rebuild()