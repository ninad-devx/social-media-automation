"""Shared response envelope.

React receives exactly one shape for success and one for failure, no matter
what n8n or Buffer actually returned.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Stable machine-readable error code")
    message: str = Field(..., description="User-safe message, safe to display in the UI")
    field: str | None = Field(default=None, description="Request field the error relates to")


class ErrorResponse(BaseModel):
    success: bool = False
    request_id: str
    error: ErrorDetail
    # All errors collected during validation, when there is more than one.
    errors: list[ErrorDetail] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    n8n_configured: bool
    database: str
    accounts_seeded: bool