"""The `{ data, error, meta }` response envelope shared by every Clinchec API.

Kept per-service rather than in a shared package on purpose: each service image
builds from its own directory as its Docker context, so a cross-service Python
import would mean either a monorepo-wide build context or a private package
index. The contract is small and stable enough that duplication is the cheaper
trade — and `packages/shared-types` holds the single source of truth that the
TypeScript side validates against.
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
    code: str
    message: str
    details: dict[str, Any] | None = None


class Meta(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str = "clinchec-forms"
    version: str = "0.1.0"
    duration_ms: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta = Field(default_factory=Meta)


class ApiError(Exception):
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


def _meta(request: Request | None) -> Meta:
    meta = Meta()
    if request is not None:
        meta.request_id = getattr(request.state, "request_id", meta.request_id)
        meta.service = request.app.state.service_name
        meta.version = request.app.state.service_version
    return meta


def ok(data: T, *, request: Request | None = None, **meta_extra: Any) -> Envelope[T]:
    meta = _meta(request)
    meta.extra = meta_extra
    return Envelope[T](data=data, error=None, meta=meta)


def fail(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    envelope = Envelope[Any](
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=_meta(request),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))
