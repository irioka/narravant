"""Spike & Stabilize tests for storage (CAS) and database (optimistic locking, sqlite-vec)."""

import pytest

from narravant.db.database import (
    DatabaseManager,
    DocumentRepository,
    DocumentSort,
    OptimisticLockError,
)
from narravant.storage.gcs import GcsConflictError, InMemoryScriptStorageClient


def test_gcs_cas_precondition_and_conflict():
    storage = InMemoryScriptStorageClient()
    doc_id = "doc-cas-test"
    initial_payload = {"schema_version": 1, "title": "Initial"}

    # Initial write with if_generation_match=0 (object must not exist)
    first = storage.write_structured_script(doc_id, 1, initial_payload, if_generation_match=0)
    assert first.generation > 0

    # Conflicting initial write (object now exists, match=0 must fail with 409)
    with pytest.raises(GcsConflictError) as exc_info:
        storage.write_structured_script(doc_id, 1, {"title": "Conflict"}, if_generation_match=0)
    assert exc_info.value.expected_generation == 0

    # Successful update with matching generation
    updated_payload = {"schema_version": 1, "title": "Updated"}
    second = storage.write_structured_script(doc_id, 1, updated_payload, if_generation_match=first.generation)
    assert second.generation > first.generation

    # Stale generation update must fail
    with pytest.raises(GcsConflictError):
        storage.write_structured_script(doc_id, 1, {"title": "Stale"}, if_generation_match=first.generation)


def test_schema_has_no_sharing():
    db = DatabaseManager(":memory:")
    db.init_schema()
    with db.session() as conn:
        user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        assert {"user_id", "email", "display_name", "created_at", "updated_at"} <= user_cols

        doc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "is_public" not in doc_cols
        assert "sharing_version" not in doc_cols

        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "document_shares" not in tables


def test_sqlite_optimistic_locking_prevents_lost_updates():
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("u-1", "u1@example.com", "User 1")

    # Create document (version_id=1)
    doc = repo.create_document(
        document_id="doc-opt-1",
        owner_user_id="u-1",
        title="Original Draft",
        gcs_uri="gs://test/v1.json",
        gcs_generation=1,
        payload_sha256="synthetic-opt-hash",
    )
    assert doc["version_id"] == 1
    with db.session() as conn:
        version = conn.execute(
            """
            SELECT gcs_generation, payload_sha256, title
            FROM document_versions
            WHERE document_id = ? AND version_id = 1
            """,
            ("doc-opt-1",),
        ).fetchone()
    assert dict(version) == {
        "gcs_generation": 1,
        "payload_sha256": "synthetic-opt-hash",
        "title": "Original Draft",
    }

    # First client updates with expected_version=1 -> succeeds, bumps to 2
    repo.update_document("doc-opt-1", expected_version=1, title="Draft v2")
    current = repo.get_document("doc-opt-1")
    assert current["version_id"] == 2
    assert current["title"] == "Draft v2"

    # Second concurrent client tries updating with stale expected_version=1 -> raises OptimisticLockError
    with pytest.raises(OptimisticLockError) as exc_info:
        repo.update_document("doc-opt-1", expected_version=1, title="Conflicting Draft")
    assert exc_info.value.expected_version == 1


def test_sqlite_vec_cosine_distance_query():
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("u-1", "u1@example.com", "User 1")

    vec_a = [1.0] + [0.0] * 9
    vec_b = [0.0] * 9 + [1.0]

    repo.create_document("doc-a", "u-1", "Doc A", "gs://test/a.json", 1, "synthetic-a-hash", valence_vector=vec_a)
    repo.create_document("doc-b", "u-1", "Doc B", "gs://test/b.json", 1, "synthetic-b-hash", valence_vector=vec_b)

    # Search for vector identical to vec_a
    results, total = repo.list_documents(similar_to_vector=vec_a, limit=10)
    assert total == 2
    # First match should be Doc A with distance near 0.0
    assert results[0]["document_id"] == "doc-a"
    assert results[0]["arc_distance"] < 0.001
    assert results[0]["expected_version"] == 1

    # Search with max_arc_distance threshold filters out dissimilar doc-b
    filtered, filtered_total = repo.list_documents(similar_to_vector=vec_a, max_arc_distance=0.5, limit=10)
    assert filtered_total == 1
    assert len(filtered) == 1
    assert filtered[0]["document_id"] == "doc-a"
    assert filtered[0]["arc_distance"] < 0.001


def test_init_schema_creates_the_10_dimension_valence_index() -> None:
    db = DatabaseManager(":memory:")
    db.init_schema()

    with db.session() as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'valence_vectors'"
        ).fetchone()

    assert row is not None
    assert "valence_vector float[10]" in row["sql"]


def test_document_list_uses_stable_sql_multi_sort_and_share_counts():
    """一覧のページング結果は SQL の複数列ソートで安定する。"""
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("u-a", "alpha@example.com", "Alpha")
    repo.ensure_user("u-b", "bravo@example.com", "Bravo")

    for document_id, owner_user_id, title in (
        ("doc-alpha-z", "u-a", "Zulu"),
        ("doc-alpha-a", "u-a", "Alpha"),
        ("doc-bravo-z", "u-b", "Zulu"),
        ("doc-bravo-a", "u-b", "Alpha"),
    ):
        repo.create_document(
            document_id,
            owner_user_id,
            title,
            f"gs://test/{document_id}.json",
            1,
            f"synthetic-{document_id}-hash",
        )

    # List documents with multi-sort
    first_page, total = repo.list_documents(
        limit=2,
        sorts=[DocumentSort("owner_email", "asc"), DocumentSort("title", "desc")],
    )
    second_page, _ = repo.list_documents(
        limit=2,
        offset=2,
        sorts=[DocumentSort("owner_email", "asc"), DocumentSort("title", "desc")],
    )

    assert total == 4
    assert [row["document_id"] for row in first_page + second_page] == [
        "doc-alpha-z",
        "doc-alpha-a",
        "doc-bravo-z",
        "doc-bravo-a",
    ]
    assert first_page[0]["owner_email"] == "alpha@example.com"
    assert first_page[0]["shared_count"] == 0
    assert first_page[0]["expected_version"] == 1

    from narravant.api.schemas import DocumentItem

    assert DocumentItem(**first_page[0]).expected_version == 1


def test_document_sharing_methods_absent() -> None:
    repo = DocumentRepository(DatabaseManager(":memory:"))
    assert not hasattr(repo, "get_document_sharing")
    assert not hasattr(repo, "replace_document_sharing")
    assert not hasattr(repo, "has_document_view_grant")
