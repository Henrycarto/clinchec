"""The `{ data, error, meta }` response envelope shared by every Clinchec API.

Every route — success or failure — returns this shape, so the frontend has a
single unwrap path and error handling never depends on the status code alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code, e.g. 'note_too_short'.")
    message: str = Field(description="Human-readable explanation safe to surface in the UI.")
    details: dict[str, Any] | None = None


class Meta(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str = "clinchec-scan"
    version: str = "0.1.0"
    duration_ms: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta = Field(default_factory=Meta)


class ApiError(Exception):
    """Raised anywhere in the service to produce an enveloped error response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def ok(data: T, *, request: Request | None = None, **meta_extra: Any) -> Envelope[T]:
    meta = Meta(extra=meta_extra)
    if request is not None:
        meta.request_id = getattr(request.state, "request_id", meta.request_id)
        meta.service = request.app.state.service_name
        meta.version = request.app.state.service_version
    return Envelope[T](data=data, error=None, meta=meta)


def fail(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    meta = Meta()
    if request is not None:
        meta.request_id = getattr(request.state, "request_id", meta.request_id)
        meta.service = request.app.state.service_name
        meta.version = request.app.state.service_version
    envelope = Envelope[Any](
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta,
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))
