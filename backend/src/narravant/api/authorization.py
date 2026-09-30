"""Existence-hiding document and task authorization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import status

from narravant.api.dependencies import CurrentUser
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.db.database import DocumentRepository

DOCUMENT_NOT_FOUND_MESSAGE = "指定されたドキュメントが見つかりません。"
TASK_NOT_FOUND_MESSAGE = "指定されたタスクが見つかりません。"


@dataclass(frozen=True)
class DocumentAccess:
    document: dict[str, Any]
    is_owner: bool


def document_not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, ApiErrorCode.DOCUMENT_NOT_FOUND, DOCUMENT_NOT_FOUND_MESSAGE)


def task_not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, ApiErrorCode.TASK_NOT_FOUND, TASK_NOT_FOUND_MESSAGE)


def authorize_document_read(repo: DocumentRepository, document_id: str, user: CurrentUser) -> DocumentAccess:
    document = repo.get_document(document_id)
    if document is None:
        raise document_not_found()
    return DocumentAccess(document=document, is_owner=True)


def authorize_document_write(repo: DocumentRepository, document_id: str, user: CurrentUser) -> DocumentAccess:
    return authorize_document_read(repo, document_id, user)
