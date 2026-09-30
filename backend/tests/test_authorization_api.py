"""Single-user authorization contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from narravant.api.dependencies import LOCAL_USER_DISPLAY_NAME, LOCAL_USER_EMAIL, LOCAL_USER_ID, CurrentUser
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.storage.gcs import InMemoryScriptStorageClient


@pytest.fixture
def repository() -> DocumentRepository:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    repo.create_document("owned", LOCAL_USER_ID, "Owned", "memory://owned", 1, "a" * 64)
    return repo


def test_document_read_and_write_authorizes_existing_document(
    repository: DocumentRepository,
) -> None:
    from narravant.api.authorization import authorize_document_read, authorize_document_write

    user = CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    read_access = authorize_document_read(repository, "owned", user)
    assert read_access.is_owner is True
    assert read_access.document["document_id"] == "owned"

    write_access = authorize_document_write(repository, "owned", user)
    assert write_access.is_owner is True
    assert write_access.document["document_id"] == "owned"


def test_document_read_and_write_rejects_missing_document(
    repository: DocumentRepository,
) -> None:
    from narravant.api.authorization import authorize_document_read, authorize_document_write

    user = CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    for fn in (authorize_document_read, authorize_document_write):
        with pytest.raises(ApiError) as caught:
            fn(repository, "non-existent-doc", user)
        assert caught.value.status_code == 404
        assert caught.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND


def test_document_capabilities_expose_single_user_permissions(
    repository: DocumentRepository,
) -> None:
    from narravant.api.documents import _document_capabilities

    doc = repository.get_document("owned")
    assert doc is not None

    user = CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    capabilities = _document_capabilities(doc, user)
    assert capabilities.can_edit is True
    assert capabilities.can_share is False
    assert capabilities.can_delete is True


@pytest.mark.asyncio
async def test_missing_document_cannot_trigger_storage_reads(
    repository: DocumentRepository,
) -> None:
    from narravant.api.documents import (
        get_document_detail,
        get_document_version,
        list_document_versions,
    )
    from narravant.api.emotion_arc import get_emotion_arc_similarities

    class CountingStorage(InMemoryScriptStorageClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read_structured_script(self, document_id: str, version_id: int):
            self.calls += 1
            return super().read_structured_script(document_id, version_id)

    storage = CountingStorage()
    user = CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    settings = SimpleNamespace(valence_patterns=[])

    requests = (
        lambda: get_document_detail("non-existent", repository, storage, user),
        lambda: list_document_versions("non-existent", repository, user),
        lambda: get_document_version("non-existent", 1, repository, storage, user),
        lambda: get_emotion_arc_similarities("non-existent", user, repository, storage, settings),
    )
    for request in requests:
        with pytest.raises(ApiError) as caught:
            await request()
        assert caught.value.status_code == 404
        assert caught.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND
    assert storage.calls == 0
