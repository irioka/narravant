from __future__ import annotations

from datetime import UTC, datetime

import pytest

from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.services.voice_models import StoredVoiceRecord


def _record(
    owner: str,
    *,
    traits: str | None = "warm narrator",
    expire_time: str | None = "2027-10-01T00:00:00+00:00",
    availability: str = "available",
) -> StoredVoiceRecord:
    now = datetime.now(UTC).isoformat()
    return StoredVoiceRecord(
        owner_user_id=owner,
        voice_id="voice_shared_1",
        speaker_label="Narrator",
        voice_traits=traits,
        language_code="ja-JP",
        created_at=now,
        created_at_source="local_response",
        registered_at=now,
        expire_time=expire_time,
        last_checked_at=now,
        availability=availability,  # type: ignore[arg-type]
    )


@pytest.fixture
def database() -> DatabaseManager:
    db = DatabaseManager(":memory:")
    db.init_schema()
    with db.session() as conn:
        conn.executemany(
            "INSERT INTO users (user_id, email, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("owner-a", "a@example.test", "A", "2026-10-15T00:00:00+00:00", "2026-10-15T00:00:00+00:00"),
                ("owner-b", "b@example.test", "B", "2026-10-15T00:00:00+00:00", "2026-10-15T00:00:00+00:00"),
            ],
        )
    return db


def test_voice_registry_deduplicates_and_updates_latest_provider_metadata(database: DatabaseManager) -> None:
    repository = VoiceRegistryRepository(database)

    repository.upsert(_record("owner-a"))
    repository.upsert(_record("owner-a", traits=None, expire_time=None))

    records = repository.list("owner-a")
    assert len(records) == 1
    assert records[0].voice_traits == "warm narrator"
    assert records[0].expire_time is None
    assert records[0].availability == "available"


def test_voice_registry_is_scoped_by_owner_and_requires_an_owner(database: DatabaseManager) -> None:
    repository = VoiceRegistryRepository(database)
    repository.upsert(_record("owner-a", traits="A traits"))
    repository.upsert(_record("owner-b", traits="B traits"))

    assert repository.get("owner-a", "voice_shared_1").voice_traits == "A traits"  # type: ignore[union-attr]
    assert repository.get("owner-b", "voices/voice_shared_1").voice_traits == "B traits"  # type: ignore[union-attr]
    assert repository.list("owner-a")[0].voice_traits == "A traits"
    assert repository.list("owner-b")[0].voice_traits == "B traits"
    assert repository.ids_registered_for_other_owners("owner-a", ["voice_shared_1"]) == {"voice_shared_1"}
    assert repository.ids_registered_for_other_owners("owner-b", ["voice_shared_1"]) == {"voice_shared_1"}
    with pytest.raises(ValueError, match="owner_user_id is required"):
        repository.list("")


def test_registry_adds_to_existing_database_without_changing_document_versions(database: DatabaseManager) -> None:
    with database.session() as conn:
        conn.execute(
            """
            INSERT INTO documents (
                document_id, owner_user_id, title, current_version_id, version_id, created_at, updated_at
            ) VALUES ('doc-1', 'owner-a', 'A story', 1, 1, '2026-10-15T00:00:00+00:00', '2026-10-15T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
            ) VALUES ('doc-1', 1, 'file://script', 7, 'synthetic-hash', 'A story', 1, '2026-10-15T00:00:00+00:00')
            """
        )
        conn.execute("DROP TABLE voice_registry")

    database.init_schema()

    with database.session() as conn:
        document = conn.execute(
            "SELECT current_version_id, version_id FROM documents WHERE document_id = 'doc-1'"
        ).fetchone()
        version = conn.execute(
            "SELECT payload_sha256, gcs_generation FROM document_versions "
            "WHERE document_id = 'doc-1' AND version_id = 1"
        ).fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(voice_registry)")}
        primary_key = tuple(
            row["name"]
            for row in sorted(conn.execute("PRAGMA table_info(voice_registry)"), key=lambda item: item["pk"])
            if row["pk"]
        )

    assert tuple(document) == (1, 1)
    assert tuple(version) == ("synthetic-hash", 7)
    assert columns == {
        "owner_user_id",
        "voice_id",
        "speaker_label",
        "voice_traits",
        "language_code",
        "created_at",
        "created_at_source",
        "registered_at",
        "expire_time",
        "last_checked_at",
        "availability",
    }
    assert primary_key == ("owner_user_id", "voice_id")


def test_init_schema_still_rejects_stale_database_sharing_table(database: DatabaseManager) -> None:
    with database.session() as conn:
        conn.execute("CREATE TABLE document_shares (document_id TEXT)")

    with pytest.raises(RuntimeError, match="document_shares must not exist"):
        database.init_schema()


def test_registry_persists_after_database_manager_restart(tmp_path) -> None:
    db_path = str(tmp_path / "narravant.sqlite3")
    first_manager = DatabaseManager(db_path)
    first_manager.init_schema()
    DocumentRepository(first_manager).ensure_user("owner-a", "a@example.test", "A")
    record = _record("owner-a")
    VoiceRegistryRepository(first_manager).upsert(record)

    restarted_manager = DatabaseManager(db_path)
    restarted_manager.init_schema()

    assert VoiceRegistryRepository(restarted_manager).get("owner-a", "voice_shared_1") == record
