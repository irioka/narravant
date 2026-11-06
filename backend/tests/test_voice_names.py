import asyncio
import threading

import pytest

from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_names import VoiceNameRepository
from narravant.services.voice_naming import choose_available_name, normalize_name_key


def test_names_compare_normalized_and_skip_existing_suffix():
    occupied = {normalize_name_key(name) for name in ("Ｗａｒｍ", "warm #2")}
    assert choose_available_name("Warm", occupied) == "Warm #3"


@pytest.mark.asyncio
async def test_independent_connections_reserve_unique_names(tmp_path):
    path = str(tmp_path / "names.sqlite3")
    first, second = DatabaseManager(path), DatabaseManager(path)
    first.init_schema()
    documents = DocumentRepository(first)
    documents.ensure_user("owner-a", "a@example.test", "A")
    documents.ensure_user("owner-b", "b@example.test", "B")
    repositories = [VoiceNameRepository(first), VoiceNameRepository(second)]
    barrier = threading.Barrier(2)

    def reserve(repository):
        barrier.wait(timeout=5)
        return repository.reserve("owner-a", "Warm Velvet", [])

    results = await asyncio.gather(*(asyncio.to_thread(reserve, repo) for repo in repositories))
    assert {result.display_name for result in results} == {"Warm Velvet", "Warm Velvet #2"}
    assert repositories[0].reserve("owner-b", "Warm Velvet", []).display_name == "Warm Velvet"
    with second.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM voice_name_claims WHERE owner_user_id='owner-a'").fetchone()[0] == 2
    repositories[0].mark_unknown(results[0])
    assert repositories[1].reserve("owner-a", "Warm Velvet", []).display_name == "Warm Velvet #3"


def test_provider_cache_replaces_confirmed_claim_without_releasing_concurrency_guard(tmp_path):
    from dataclasses import replace

    from narravant.db.voice_registry import VoiceRegistryRepository
    from narravant.services.voice_models import StoredVoiceRecord

    db = DatabaseManager(str(tmp_path / "provider-cache.sqlite3"))
    db.init_schema()
    DocumentRepository(db).ensure_user("owner-a", "a@example.test", "A")
    names = VoiceNameRepository(db)
    registry = VoiceRegistryRepository(db)
    reservation = names.reserve("owner-a", "Requested", [])
    record = StoredVoiceRecord(
        "owner-a", "voice_synthetic", "Alice", "Synthetic", "en-US", None, "unknown",
        "2026-11-06T00:00:00Z", None, "2026-11-06T01:00:00Z", "available", "Gemini canonical",
    )
    with db.immediate_session() as conn:
        names.confirm(reservation, record.voice_id, connection=conn)
        registry.upsert(record, connection=conn)
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM voice_name_claims").fetchone()[0] == 0
    # A creator with an older empty snapshot still sees the committed provider cache.
    assert names.reserve("owner-a", "Gemini canonical", []).display_name == "Gemini canonical #2"
    assert names.reserve("owner-a", "Requested", []).display_name == "Requested"
    reopened = VoiceRegistryRepository(DatabaseManager(db.db_path))
    assert reopened.get("owner-a", record.voice_id).display_name == "Gemini canonical"
    registry.upsert(replace(record, display_name="Stale", last_checked_at="2026-11-06T00:00:00Z"))
    assert reopened.get("owner-a", record.voice_id).display_name == "Gemini canonical"


def test_stale_provider_snapshot_does_not_reconcile_another_unknown_creation(tmp_path):
    from narravant.db.voice_registry import VoiceRegistryRepository
    from narravant.services.voice_models import StoredVoiceRecord

    db = DatabaseManager(str(tmp_path / "stale-reconcile.sqlite3"))
    db.init_schema()
    DocumentRepository(db).ensure_user("owner-a", "a@example.test", "A")
    names = VoiceNameRepository(db)
    VoiceRegistryRepository(db).upsert(StoredVoiceRecord(
        "owner-a", "voice_existing", "Alice", "Synthetic", "en-US", None, "unknown",
        "2026-11-06T00:00:00Z", None, "2026-11-06T01:00:00Z", "available", "New provider name",
    ))
    pending = names.reserve("owner-a", "Old provider name", [])
    names.mark_unknown(pending)
    names.reconcile("owner-a", [("voice_existing", "Old provider name")])
    with db.session() as conn:
        claim = conn.execute(
            "SELECT state, voice_id FROM voice_name_claims WHERE attempt_id = ?", (pending.attempt_id,)
        ).fetchone()
    assert tuple(claim) == ("unknown", None)
    assert names.reserve("owner-a", "Old provider name", []).display_name == "Old provider name #2"
