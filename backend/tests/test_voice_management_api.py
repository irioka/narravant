from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.services.voice_design import FakeVoiceDesignClient, VoiceDesignError
from narravant.services.voice_management import VoiceManagementError, VoiceManagementService
from narravant.services.voice_models import ProviderVoice, StoredVoiceRecord, VoicePage, utc_iso
from narravant.storage.gcs import InMemoryScriptStorageClient


@pytest.fixture
def management_setup():
    db = DatabaseManager(":memory:")
    db.init_schema()
    with db.session() as conn:
        conn.executemany(
            "INSERT INTO users (user_id, email, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("owner-a", "a@example.test", "A", utc_iso(), utc_iso()),
                ("owner-b", "b@example.test", "B", utc_iso(), utc_iso()),
            ],
        )
    documents = DocumentRepository(db)
    registry = VoiceRegistryRepository(db)
    storage = InMemoryScriptStorageClient()
    provider = FakeVoiceDesignClient()
    service = VoiceManagementService(
        provider,
        registry,
        documents,
        storage,
        local_owner_id="owner-a",
    )
    return db, documents, registry, storage, provider, service


def _add_document(
    db: DatabaseManager,
    storage: InMemoryScriptStorageClient,
    *,
    document_id: str,
    owner: str = "owner-a",
    title: str = "Synthetic story",
    voice_id: str | None = None,
    speaker: str = "Narrator",
    source_fountain: str = "Synthetic text only",
) -> None:
    assignments = []
    if voice_id:
        assignments = [{"speaker": speaker, "voice_id": voice_id, "voice_traits": ""}]
    stored = storage.write_structured_script(
        document_id,
        1,
        {
            "schema_version": 1,
            "document_id": document_id,
            "version_id": 1,
            "source_fountain": source_fountain,
            "voice_assignments": assignments,
        },
        if_generation_match=0,
    )
    now = utc_iso()
    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO documents (
                document_id, owner_user_id, title, current_version_id, version_id, created_at, updated_at
            ) VALUES (?, ?, ?, 1, 1, ?, ?)
            """,
            (document_id, owner, title, now, now),
        )
        conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
            ) VALUES (?, 1, ?, ?, ?, ?, 1, ?)
            """,
            (document_id, stored.uri, stored.generation, stored.sha256, title, now),
        )


def _stored_record(owner: str, voice_id: str = "voice_shared_1") -> StoredVoiceRecord:
    now = utc_iso()
    return StoredVoiceRecord(
        owner_user_id=owner,
        voice_id=voice_id,
        speaker_label="Narrator",
        voice_traits="warm and clear",
        language_code="ja-JP",
        created_at=now,
        created_at_source="local_response",
        registered_at=now,
        expire_time=utc_iso(datetime.now(UTC) + timedelta(days=120)),
        last_checked_at=now,
        availability="available",
    )


def _provider_voice(
    voice_id: str,
    *,
    expiry: datetime | None = None,
) -> ProviderVoice:
    return ProviderVoice(
        voice_id=voice_id,
        voice_type="prompted",
        language_code="ja-JP",
        expire_time=expiry or datetime.now(UTC) + timedelta(days=120),
    )


@pytest.mark.asyncio
async def test_voice_library_paginates_and_keeps_owner_metadata_local(management_setup) -> None:
    _, _, registry, storage, provider, service = management_setup
    provider.list_page_size = 1
    provider.voices = {
        "voice_shared_1": _provider_voice("voice_shared_1"),
        "voice_unassigned_2": _provider_voice("voice_unassigned_2"),
    }
    registry.upsert(_stored_record("owner-a"))
    _add_document(
        service.documents.db,
        storage,
        document_id="doc-owner-a",
        owner="owner-a",
        voice_id="voice_shared_1",
    )
    _add_document(
        service.documents.db,
        storage,
        document_id="doc-owner-b",
        owner="owner-b",
        voice_id="voice_shared_1",
    )

    result = await service.list_voices("owner-a")

    assert result.complete is True
    assert [voice.voice_id for voice in result.voices] == ["voice_shared_1", "voice_unassigned_2"]
    shared = result.voices[0]
    assert shared.voice_traits == "warm and clear"
    assert shared.usage_count == 1
    assert result.voices[1].usage_count == 0
    calls_before = provider.list_call_count
    with pytest.raises(VoiceManagementError) as raised:
        await service.list_voices("owner-b")
    assert raised.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert provider.list_call_count == calls_before


@pytest.mark.asyncio
async def test_list_failure_on_second_page_keeps_registry_snapshot(management_setup, monkeypatch) -> None:
    _, _, registry, _, provider, service = management_setup
    cached = _stored_record("owner-a")
    registry.upsert(cached)
    calls: list[str | None] = []

    async def list_page(page_token: str | None = None) -> VoicePage:
        calls.append(page_token)
        if page_token is None:
            return VoicePage((_provider_voice("voice_shared_1"),), "second-page")
        raise VoiceDesignError(
            "synthetic second page failure",
            provider_status=503,
            provider_code="UNAVAILABLE",
        )

    monkeypatch.setattr(provider, "list_voices", list_page)

    with pytest.raises(VoiceManagementError) as raised:
        await service.list_voices("owner-a")

    assert raised.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert calls == [None, "second-page"]
    assert registry.get("owner-a", "voice_shared_1") == cached


@pytest.mark.asyncio
async def test_repeated_list_page_token_fails_without_registry_changes(management_setup, monkeypatch) -> None:
    _, _, registry, _, provider, service = management_setup
    cached = _stored_record("owner-a")
    registry.upsert(cached)
    calls: list[str | None] = []

    async def list_page(page_token: str | None = None) -> VoicePage:
        calls.append(page_token)
        return VoicePage((), "loop-token")

    monkeypatch.setattr(provider, "list_voices", list_page)

    with pytest.raises(VoiceManagementError) as raised:
        await service.list_voices("owner-a")

    assert raised.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert calls == [None, "loop-token"]
    assert registry.get("owner-a", "voice_shared_1") == cached


@pytest.mark.asyncio
async def test_foreign_owner_voice_id_is_never_imported_or_managed(management_setup) -> None:
    _, _, registry, storage, provider, service = management_setup
    foreign = _stored_record("owner-b")
    registry.upsert(foreign)
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1")
    _add_document(
        service.documents.db,
        storage,
        document_id="doc-owner-a-foreign-id",
        owner="owner-a",
        voice_id="voice_shared_1",
    )

    listed = await service.list_voices("owner-a")
    assert listed.voices == []
    assert registry.get("owner-a", "voice_shared_1") is None
    assert registry.get("owner-b", "voice_shared_1") == foreign

    operations = (
        lambda: service.get_voice("owner-a", "voice_shared_1"),
        lambda: service.get_usages("owner-a", "voice_shared_1"),
        lambda: service.delete_voice("owner-a", "voice_shared_1"),
    )
    for operation in operations:
        with pytest.raises(VoiceManagementError) as raised:
            await operation()
        assert raised.value.code == "VOICE_NOT_FOUND"

    assert provider.get_call_count == 0
    assert provider.delete_call_count == 0
    assert "voice_shared_1" in provider.voices


@pytest.mark.asyncio
async def test_failed_get_marks_list_response_stale_without_overwriting_cached_check(
    management_setup, monkeypatch
) -> None:
    from datetime import timedelta

    _, _, registry, _, provider, service = management_setup
    cached = StoredVoiceRecord(
        **{
            **_stored_record("owner-a").__dict__,
            "last_checked_at": utc_iso(datetime.now(UTC) - timedelta(minutes=1)),
        }
    )
    registry.upsert(cached)

    async def empty_page(page_token: str | None = None) -> VoicePage:
        return VoicePage((), None)

    async def denied_get(_voice_id: str) -> ProviderVoice:
        raise VoiceDesignError("synthetic permission failure", provider_status=403, provider_code="PERMISSION_DENIED")

    monkeypatch.setattr(provider, "list_voices", empty_page)
    monkeypatch.setattr(provider, "get_voice", denied_get)

    result = await service.list_voices("owner-a")

    assert result.complete is False
    assert result.voices[0].availability == "unknown"
    assert result.voices[0].expiry_state == "stale"
    assert result.voices[0].last_checked_at == cached.last_checked_at
    assert registry.get("owner-a", "voice_shared_1") == cached


@pytest.mark.asyncio
async def test_expiry_refresh_handles_missing_expiry_cache_age_and_thirty_day_boundary(
    management_setup,
) -> None:
    _, _, registry, _, provider, service = management_setup
    soon = datetime.now(UTC) + timedelta(days=29)
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1", expiry=soon)
    provider.voices["voice_unknown_expiry"] = ProviderVoice(
        voice_id="voice_unknown_expiry",
        voice_type="prompted",
        language_code="ja-JP",
        expire_time=None,
    )
    previous = StoredVoiceRecord(
        **{
            **_stored_record("owner-a").__dict__,
            "expire_time": utc_iso(datetime.now(UTC) + timedelta(days=200)),
        }
    )
    registry.upsert(previous)

    result = await service.list_voices("owner-a")
    by_id = {voice.voice_id: voice for voice in result.voices}

    assert by_id["voice_shared_1"].expiry_state == "expiring"
    assert by_id["voice_shared_1"].expire_time == utc_iso(soon)
    assert registry.get("owner-a", "voice_shared_1").expire_time == utc_iso(soon)
    assert by_id["voice_unknown_expiry"].expiry_state == "unknown"
    assert registry.get("owner-a", "voice_unknown_expiry").expire_time is None

    old_cache = StoredVoiceRecord(
        **{
            **_stored_record("owner-a").__dict__,
            "last_checked_at": utc_iso(datetime.now(UTC) - timedelta(minutes=6)),
        }
    )
    assert service._expiry_state(old_cache) == "stale"
    outside_threshold = StoredVoiceRecord(
        **{
            **_stored_record("owner-a").__dict__,
            "expire_time": utc_iso(datetime.now(UTC) + timedelta(days=31)),
        }
    )
    assert service._expiry_state(outside_threshold) == "valid"


@pytest.mark.asyncio
async def test_usage_scan_reads_all_51_current_documents(management_setup) -> None:
    db, _, _, storage, _, service = management_setup
    for index in range(51):
        _add_document(
            db,
            storage,
            document_id=f"doc-{index:02d}",
            title=f"Story {index:02d}",
            voice_id="voice_shared_1",
        )

    response = await service.get_usages("owner-a", "voice_shared_1")

    assert response.complete is True
    assert len(response.usages) == 51
    assert {usage.document_id for usage in response.usages} == {f"doc-{index:02d}" for index in range(51)}


@pytest.mark.asyncio
async def test_usage_scan_ignores_voice_assignments_in_noncurrent_versions(management_setup) -> None:
    db, _, _, storage, _, service = management_setup
    document_id = "doc-current-version-only"
    _add_document(db, storage, document_id=document_id, voice_id="voice_archived_1")
    current = storage.write_structured_script(
        document_id,
        2,
        {
            "schema_version": 1,
            "document_id": document_id,
            "version_id": 2,
            "source_fountain": "Synthetic current version",
            "voice_assignments": [{"speaker": "Narrator", "voice_id": "voice_current_2", "voice_traits": ""}],
        },
        if_generation_match=0,
    )
    now = utc_iso()
    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
            ) VALUES (?, 2, ?, ?, ?, ?, 1, ?)
            """,
            (document_id, current.uri, current.generation, current.sha256, "Synthetic story", now),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = 2, version_id = 2 WHERE document_id = ?",
            (document_id,),
        )

    usages, complete = service._scan_usages("owner-a")

    assert complete is True
    assert [(voice_id, usage.document_id) for voice_id, usage in usages] == [("voice_current_2", document_id)]


@pytest.mark.asyncio
async def test_usage_scan_includes_voice_assigned_only_to_listener_reaction(management_setup) -> None:
    db, _, _, storage, _, service = management_setup
    source_fountain = (
        "INT. ROOM - DAY #1#\n\n"
        "[[listener:Guide]]\n"
        "@Narrator\n"
        "この先で待って。 |わかった|\n"
    )
    _add_document(
        db,
        storage,
        document_id="doc-listener-only",
        voice_id="voice_listener_only",
        speaker="Guide",
        source_fountain=source_fountain,
    )

    usages, complete = service._scan_usages("owner-a")

    assert complete is True
    assert [(voice_id, usage.speaker) for voice_id, usage in usages] == [("voice_listener_only", "Guide")]


@pytest.mark.asyncio
async def test_delete_refuses_when_usage_scan_is_incomplete(management_setup) -> None:
    db, _, registry, storage, provider, service = management_setup
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1")
    registry.upsert(_stored_record("owner-a"))
    _add_document(db, storage, document_id="doc-readable", voice_id="voice_shared_1")
    _add_document(db, storage, document_id="doc-unreadable", voice_id="voice_shared_1")
    original_read = storage.read_structured_script

    def fail_one_document(document_id: str, version_id: int):
        if document_id == "doc-unreadable":
            raise OSError("synthetic storage failure")
        return original_read(document_id, version_id)

    storage.read_structured_script = fail_one_document  # type: ignore[method-assign]

    with pytest.raises(VoiceManagementError) as raised:
        await service.delete_voice("owner-a", "voice_shared_1")

    assert raised.value.code == "VOICE_USAGE_UNAVAILABLE"
    assert provider.delete_call_count == 0
    assert registry.get("owner-a", "voice_shared_1") is not None


@pytest.mark.asyncio
async def test_delete_removes_registry_after_provider_success_and_keeps_document_payload(management_setup) -> None:
    db, _, registry, storage, provider, service = management_setup
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1")
    registry.upsert(_stored_record("owner-a"))
    _add_document(db, storage, document_id="doc-delete", voice_id="voice_shared_1")
    before_payload, before_metadata = storage.read_structured_script("doc-delete", 1)
    with db.session() as conn:
        before_document = tuple(
            conn.execute(
                "SELECT current_version_id, version_id FROM documents WHERE document_id = 'doc-delete'"
            ).fetchone()
        )
        before_version = tuple(
            conn.execute(
                "SELECT payload_sha256, gcs_generation FROM document_versions WHERE document_id = 'doc-delete'"
            ).fetchone()
        )

    await service.delete_voice("owner-a", "voice_shared_1")

    after_payload, after_metadata = storage.read_structured_script("doc-delete", 1)
    with db.session() as conn:
        after_document = tuple(
            conn.execute(
                "SELECT current_version_id, version_id FROM documents WHERE document_id = 'doc-delete'"
            ).fetchone()
        )
        after_version = tuple(
            conn.execute(
                "SELECT payload_sha256, gcs_generation FROM document_versions WHERE document_id = 'doc-delete'"
            ).fetchone()
        )
    assert provider.delete_call_count == 1
    assert provider.voices == {}
    assert registry.get("owner-a", "voice_shared_1") is None
    assert after_payload == before_payload
    assert after_metadata == before_metadata
    assert after_document == before_document == (1, 1)
    assert after_version == before_version


@pytest.mark.asyncio
async def test_delete_removes_registry_when_provider_voice_is_already_missing(management_setup) -> None:
    db, _, registry, storage, provider, service = management_setup
    registry.upsert(_stored_record("owner-a"))
    _add_document(db, storage, document_id="doc-delete-missing", voice_id="voice_shared_1")
    before_payload = storage.read_structured_script("doc-delete-missing", 1)

    await service.delete_voice("owner-a", "voice_shared_1")

    assert provider.delete_call_count == 1
    assert registry.get("owner-a", "voice_shared_1") is None
    assert storage.read_structured_script("doc-delete-missing", 1) == before_payload


@pytest.mark.asyncio
async def test_delete_provider_failure_preserves_registry_and_document(management_setup, monkeypatch) -> None:
    db, documents, registry, storage, provider, service = management_setup
    cached = _stored_record("owner-a")
    registry.upsert(cached)
    _add_document(db, storage, document_id="doc-delete-failure", voice_id="voice_shared_1")
    before_payload = storage.read_structured_script("doc-delete-failure", 1)
    before_document = documents.get_document("doc-delete-failure")
    failures = [
        (
            VoiceDesignError("denied", provider_status=403, provider_code="PERMISSION_DENIED"),
            "VOICE_PROVIDER_PERMISSION_DENIED",
            502,
        ),
        (
            VoiceDesignError("unavailable", provider_status=503, provider_code="UNAVAILABLE"),
            "VOICE_PROVIDER_UNAVAILABLE",
            503,
        ),
        (TimeoutError(), "VOICE_PROVIDER_UNAVAILABLE", 503),
    ]

    for failure, code, status_code in failures:

        async def fail_delete(_voice_id: str, error=failure) -> None:
            if isinstance(error, TimeoutError):
                raise TimeoutError
            raise error

        monkeypatch.setattr(provider, "delete_voice", fail_delete)
        with pytest.raises(VoiceManagementError) as raised:
            await service.delete_voice("owner-a", "voice_shared_1")

        assert raised.value.code == code
        assert raised.value.status_code == status_code
        assert registry.get("owner-a", "voice_shared_1") == cached
        assert storage.read_structured_script("doc-delete-failure", 1) == before_payload
        assert documents.get_document("doc-delete-failure") == before_document


@pytest.mark.asyncio
async def test_delete_registry_failure_reports_partial_success_without_retry_or_document_changes(
    management_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, documents, registry, storage, provider, service = management_setup
    cached = _stored_record("owner-a")
    registry.upsert(cached)
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1")
    _add_document(db, storage, document_id="doc-delete-partial", voice_id="voice_shared_1")
    before_payload = storage.read_structured_script("doc-delete-partial", 1)
    before_document = documents.get_document("doc-delete-partial")

    def fail_delete(_owner_id: str, _voice_id: str) -> None:
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(registry, "delete", fail_delete)

    with pytest.raises(VoiceManagementError) as raised:
        await service.delete_voice("owner-a", "voice_shared_1")

    assert raised.value.code == "VOICE_REGISTRY_SYNC_FAILED"
    assert provider.delete_call_count == 1
    assert provider.voices == {}
    assert registry.get("owner-a", "voice_shared_1") == cached
    assert storage.read_structured_script("doc-delete-partial", 1) == before_payload
    assert documents.get_document("doc-delete-partial") == before_document


@pytest.mark.asyncio
async def test_provider_list_failure_does_not_mark_cached_voices_missing(management_setup) -> None:
    _, _, registry, _, provider, service = management_setup
    record = _stored_record("owner-a")
    registry.upsert(record)
    provider.should_fail = True

    with pytest.raises(VoiceManagementError) as raised:
        await service.list_voices("owner-a")

    assert raised.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert registry.get("owner-a", "voice_shared_1") == record
    assert provider.delete_call_count == 0


@pytest.mark.asyncio
async def test_playback_validation_blocks_missing_voice_before_tts_boundary(management_setup) -> None:
    db, _, registry, storage, provider, service = management_setup
    registry.upsert(_stored_record("owner-a"))
    _add_document(db, storage, document_id="doc-play", voice_id="voice_shared_1")

    with pytest.raises(VoiceManagementError) as raised:
        await service.validate_voice_ids("owner-a", ["voice_shared_1"])

    assert raised.value.code == "VOICE_NOT_FOUND"
    assert provider.get_call_count == 1
    assert provider.call_count == 0
    assert registry.get("owner-a", "voice_shared_1").availability == "missing"


@pytest.mark.asyncio
async def test_playback_validation_rejects_expired_voice_after_fresh_get(management_setup) -> None:
    _, _, registry, _, provider, service = management_setup
    expired = datetime.now(UTC) - timedelta(minutes=1)
    provider.voices["voice_shared_1"] = _provider_voice("voice_shared_1", expiry=expired)
    registry.upsert(_stored_record("owner-a"))

    with pytest.raises(VoiceManagementError) as raised:
        await service.validate_voice_ids("owner-a", ["voice_shared_1"])

    assert raised.value.code == "VOICE_EXPIRED"
    assert provider.get_call_count == 1
    assert registry.get("owner-a", "voice_shared_1").expire_time < utc_iso()


@pytest.mark.asyncio
async def test_voice_management_route_handlers_expose_list_usages_detail_and_delete() -> None:
    from narravant.api.dependencies import LOCAL_USER_DISPLAY_NAME, LOCAL_USER_EMAIL, LOCAL_USER_ID, CurrentUser
    from narravant.api.voice_schemas import VoiceUsage
    from narravant.api.voices import (
        delete_stored_voice,
        get_stored_voice,
        get_stored_voice_usages,
        list_stored_voices,
    )

    db = DatabaseManager(":memory:")
    db.init_schema()
    DocumentRepository(db).ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    registry = VoiceRegistryRepository(db)
    storage = InMemoryScriptStorageClient()
    provider = FakeVoiceDesignClient()
    provider.voices["voice_http_1"] = _provider_voice("voice_http_1")
    registry.upsert(_stored_record(LOCAL_USER_ID, "voice_http_1"))
    _add_document(
        db,
        storage,
        document_id="doc-http",
        owner=LOCAL_USER_ID,
        voice_id="voice_http_1",
    )
    service = VoiceManagementService(
        provider,
        registry,
        DocumentRepository(db),
        storage,
        local_owner_id=LOCAL_USER_ID,
    )
    user = CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    listed = await list_stored_voices(service, user)
    usages = await get_stored_voice_usages("voice_http_1", service, user)
    detail = await get_stored_voice("voice_http_1", service, user)
    deleted = await delete_stored_voice("voice_http_1", service, user)

    assert listed.complete is True
    assert listed.voices[0].voice_id == "voice_http_1"
    assert usages.usages == [VoiceUsage(document_id="doc-http", title="Synthetic story", speaker="Narrator")]
    assert usages.complete is True
    assert detail.availability == "available"
    assert deleted.status_code == 204
    assert registry.get(LOCAL_USER_ID, "voice_http_1") is None


@pytest.mark.asyncio
async def test_create_voice_registers_expiry_without_changing_public_response(management_setup) -> None:
    from fastapi import Response

    from narravant.api.voices import CreateVoiceRequest, _create_voice

    _, _, registry, _, provider, service = management_setup
    response = Response()
    result = await _create_voice(
        CreateVoiceRequest(speaker="Alice", voice_traits="warm voice"),
        provider,
        registry,
        "owner-a",
        response,
        service.lock_for_owner("owner-a"),
    )

    record = registry.get("owner-a", result.voice_id)
    assert result.model_dump() == {
        "speaker": "Alice",
        "voice_id": result.voice_id,
        "voice_traits": "warm voice",
    }
    assert record is not None
    assert record.expire_time == utc_iso(provider.voices[result.voice_id].expire_time)
    assert record.created_at_source == "local_response"
    assert response.headers.get("X-Voice-Registry-Sync") is None
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_create_voice_registry_failure_returns_pending_without_retrying_or_touching_documents(
    management_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import Response

    from narravant.api.voices import CreateVoiceRequest, _create_voice

    db, documents, registry, storage, provider, service = management_setup
    _add_document(db, storage, document_id="doc-create-partial", voice_id="voice_existing_1")
    before = documents.get_document("doc-create-partial")

    def fail_upsert(_record: StoredVoiceRecord) -> None:
        raise OSError("synthetic database failure")

    monkeypatch.setattr(registry, "upsert", fail_upsert)
    response = Response()
    result = await _create_voice(
        CreateVoiceRequest(speaker="Alice", voice_traits="warm voice"),
        provider,
        registry,
        "owner-a",
        response,
        service.lock_for_owner("owner-a"),
    )

    assert result.voice_id.startswith("voice_fake_")
    assert response.headers["X-Voice-Registry-Sync"] == "pending"
    assert provider.call_count == 1
    assert registry.get("owner-a", result.voice_id) is None
    assert documents.get_document("doc-create-partial") == before


@pytest.mark.asyncio
async def test_invalid_voice_preview_stops_before_tts(management_setup) -> None:
    from narravant.api.errors import ApiError
    from narravant.api.voices import VoicePreviewRequest, _preview_voice

    db, _, registry, storage, _, service = management_setup
    registry.upsert(_stored_record("owner-a"))
    _add_document(db, storage, document_id="doc-preview", voice_id="voice_shared_1")

    class CountingTts:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def synthesize_stream(self, *, text: str, voice_id: str, style: str):
            self.calls.append(voice_id)
            yield b"synthetic audio"

    tts = CountingTts()
    with pytest.raises(ApiError) as raised:
        await _preview_voice(
            VoicePreviewRequest(voice_id="voice_shared_1", text="Hello"),
            tts,  # type: ignore[arg-type]
            service,
            "owner-a",
            allow_current_assignment=True,
        )

    assert raised.value.code == "VOICE_NOT_FOUND"
    assert tts.calls == []
