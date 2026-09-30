"""Common, content-safe API error envelope and exception handlers."""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApiErrorCode(StrEnum):
    """Stable API error codes consumed by the web client."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    INVALID_ENCODING = "INVALID_ENCODING"
    MALFORMED_FDX = "MALFORMED_FDX"
    MALFORMED_PDF = "MALFORMED_PDF"
    INVALID_NATIVE_DOCUMENT = "INVALID_NATIVE_DOCUMENT"
    PROCESS_RESTARTED = "PROCESS_RESTARTED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTHENTICATION_UNAVAILABLE = "AUTHENTICATION_UNAVAILABLE"
    ACCOUNT_BINDING_CONFLICT = "ACCOUNT_BINDING_CONFLICT"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    DOCUMENT_CONTENT_MISSING = "DOCUMENT_CONTENT_MISSING"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiErrorDetail(BaseModel):
    code: ApiErrorCode
    message: str
    context: dict[str, Any] | None = None


class ApiErrorEnvelope(BaseModel):
    error: ApiErrorDetail


class ApiError(Exception):
    """Known error that is safe to expose through the common envelope."""

    def __init__(
        self,
        status_code: int,
        code: ApiErrorCode,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.context = context


def _envelope(code: ApiErrorCode, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return ApiErrorEnvelope(error=ApiErrorDetail(code=code, message=message, context=context)).model_dump(mode="json")


def _status_code_to_error_code(status_code: int) -> ApiErrorCode:
    if status_code == status.HTTP_404_NOT_FOUND:
        return ApiErrorCode.NOT_FOUND
    if status_code == status.HTTP_403_FORBIDDEN:
        return ApiErrorCode.FORBIDDEN
    if status_code == status.HTTP_409_CONFLICT:
        return ApiErrorCode.CONFLICT
    return ApiErrorCode.VALIDATION_ERROR


def register_error_handlers(app: FastAPI) -> None:
    """Install shared handlers without logging screenplay text or request bodies."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        logger.error(
            "API validation failed path=%s fields=%s content_type=%s content_length=%s",
            request.url.path,
            fields,
            request.headers.get("content-type"),
            request.headers.get("content-length"),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope(ApiErrorCode.VALIDATION_ERROR, "入力値を確認してください。", {"fields": fields}),
        )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        logger.error(
            "API request failed path=%s code=%s status=%s message=%s",
            request.url.path,
            exc.code,
            exc.status_code,
            exc.message,
        )
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code, exc.message, exc.context))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = _status_code_to_error_code(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else "リクエストを処理できませんでした。"
        logger.error("HTTP API request failed path=%s code=%s status=%s", request.url.path, code, exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, message))

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        correlation_id = str(uuid.uuid4())
        logger.critical("Unhandled runtime error correlation_id=%s", correlation_id, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                ApiErrorCode.INTERNAL_ERROR,
                "実行時エラーが発生しました。時間をおいて再試行してください。",
                {"correlation_id": correlation_id},
            ),
        )
