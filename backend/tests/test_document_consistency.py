"""保存済み文書のSQLite/GCS整合性に対する回帰テスト。"""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest
from scripts.audit_document_consistency import validate_payload

from narravant.api.dependencies import CurrentUser
from narravant.api.documents import get_document_detail, update_document
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.api.schemas import DocumentUpdateRequest
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.storage.gcs import InMemoryScriptStorageClient

DEFAULT_USER = CurrentUser("u-default-001", "default@narravant.local")
EMOTION_ARC_SETTINGS = SimpleNamespace(emotion_arc_max_points=36)


def _seed_saved_document(
    repository: DocumentRepository,
    storage: InMemoryScriptStorageClient,
    *,
    document_id: str = "document-1",
    title: str = "Original title",
) -> str:
    """明示的なSave後に存在するv1だけを合成fixtureとして構築する。"""
    source_fountain = f"Title: {title}\n\nINT. ROOM - DAY #1#\n\nA quiet room."
    payload = {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": "u-default-001",
        "source": {
            "filename": "saved.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": source_fountain,
        "metadata": {
            "title": title,
            "logline": "検証",
            "synopsis": "静かな部屋にいる人物の検証用シーン。",
            "theme_setting": "主要なテーマ候補は選択。根拠は第1シーン",
        },
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "A quiet room.",
                "dialogues": [],
            }
        ],
        "analysis": {
            "status": "completed",
            "turning_points": [
                {
                    "tp_number": number,
                    "label": label,
                    "availability": "identified",
                    "scene_number": 1,
                    "change": f"物語の変化{number}",
                    "involved_characters": [
                        {
                            "name": "A",
                            "goal": "g",
                            "conflict": "c",
                            "choice": "ch",
                            "action": "a",
                            "change": "d",
                        }
                    ],
                    "reason": None,
                }
                for number, label in enumerate(
                    [
                        "Opportunity",
                        "Change of Plans",
                        "Point of No Return",
                        "Major Setback",
                        "Climax",
                    ],
                    start=1,
                )
            ],
            "characters": [
                {
                    "name": "A",
                    "external_goal": "外的目標",
                    "internal_need": "内的欲求",
                    "fear_or_cost": "恐れ",
                    "obstacle": "障害",
                    "choice": "選択",
                    "agency": "主体性",
                    "goal_to_outcome": "目標→結果",
                    "related_turning_points": [1],
                }
            ],
        },
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {"A": [3]},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    storage.write_search_text(document_id, source_fountain, if_generation_match=0)
    repository.create_document(
        document_id,
        "u-default-001",
        title,
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )
    return document_id


def test_preserved_analysis_rejects_a_stale_mapping_after_scene_count_changes() -> None:
    """Accepting a 37-scene map after a 38th scene was added would lose scene 38."""
    from narravant.api.documents import _validate_preserved_analysis

    payload = {
        "analysis": {
            "status": "completed",
            "characters": [{"name": "A"}],
        },
        "scenes": [{"scene_number": number} for number in range(1, 39)],
        "emotion_arc": {
            "valence": [4] * 36,
            "tension": [0] * 36,
            "characters": {"A": [4] * 36},
            "scene_mapping": scene_mapping_payload(37, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }

    with pytest.raises(ApiError, match="先にValenceを再分析") as caught:
        _validate_preserved_analysis(payload, max_points=36)

    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_missing_canonical_content_is_runtime_consistency_error_not_not_found(
    tmp_path,
) -> None:
    """永続メタデータ行だけが残った場合は404へ偽装しない。"""
    db = DatabaseManager(str(tmp_path / "metadata.sqlite3"))
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("owner-1", "owner@example.test", "Owner")
    repository.create_document(
        document_id="15d90359-152c-40b0-a55f-3a517ea27d69",
        owner_user_id="owner-1",
        title="SQLite row without canonical body",
        gcs_uri="gs://test-narravant-bucket/scripts/15d90359-152c-40b0-a55f-3a517ea27d69/v1.json",
        gcs_generation=1,
        payload_sha256="synthetic-consistency-hash",
        valence_vector=[0.0] * 9 + [1.0],
    )

    with pytest.raises(ApiError) as caught:
        await get_document_detail(
            "15d90359-152c-40b0-a55f-3a517ea27d69",
            repository,
            InMemoryScriptStorageClient("test-narravant-bucket"),
            CurrentUser("owner-1", "owner@example.test"),
        )

    assert caught.value.status_code == 500
    assert caught.value.code == ApiErrorCode.DOCUMENT_CONTENT_MISSING


@pytest.mark.asyncio
async def test_title_only_save_updates_document_title_without_publishing_a_content_version(tmp_path) -> None:
    db = DatabaseManager(str(tmp_path / "metadata.sqlite3"))
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("u-default-001", "default@narravant.local", "Default User")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    document_id = _seed_saved_document(repository, storage)

    updated = await update_document(
        document_id,
        DocumentUpdateRequest(expected_version=1, title="Renamed title"),
        repository,
        storage,
        DEFAULT_USER,
        EMOTION_ARC_SETTINGS,
    )

    assert updated.version_id == 1
    assert updated.current_version_id == 1
    stored_v1, _ = storage.read_structured_script(document_id, 1)
    assert stored_v1["metadata"]["title"] == "Original title"
    assert stored_v1["source_fountain"] == "Title: Original title\n\nINT. ROOM - DAY #1#\n\nA quiet room."
    current_version_id, history = repository.list_document_versions(document_id)  # type: ignore[misc]
    assert current_version_id == 1
    assert [(item["version_id"], item["title"]) for item in history] == [
        (1, "Renamed title"),
    ]


@pytest.mark.asyncio
async def test_arc_only_save_recalculates_feature_vector_and_rejects_invalid_arc(
    tmp_path,
) -> None:
    """感情アークの検証と特徴量再計算はクライアント値を信用しない。"""
    from narravant.core.valence_vector import default_valence_vectorizer

    db = DatabaseManager(str(tmp_path / "metadata.sqlite3"))
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("u-default-001", "default@narravant.local", "Default User")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    document_id = _seed_saved_document(repository, storage)

    saved = await update_document(
        document_id,
        DocumentUpdateRequest(
            expected_version=1,
            base_version_id=1,
            emotion_arc={
                "valence": [4],
                "tension": [0],
                "characters": {"A": [7]},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
        ),
        repository,
        storage,
        DEFAULT_USER,
        EMOTION_ARC_SETTINGS,
    )
    assert saved.emotion_arc.valence_vector == default_valence_vectorizer.vectorize([4.0])

    with pytest.raises(ApiError) as invalid_character:
        await update_document(
            document_id,
            DocumentUpdateRequest(
                expected_version=saved.expected_version,
                base_version_id=saved.version_id,
                emotion_arc={
                    "valence": [4],
                    "tension": [0],
                    "characters": {"Other": [7]},
                    "scene_mapping": scene_mapping_payload(1, 36),
                    "valence_vector": [0.0] * 9 + [1.0],
                },
            ),
            repository,
            storage,
            DEFAULT_USER,
            EMOTION_ARC_SETTINGS,
        )
    assert invalid_character.value.status_code == 422


@pytest.mark.asyncio
async def test_detail_rejects_a_structured_payload_missing_required_analysis(
    tmp_path,
) -> None:
    db = DatabaseManager(str(tmp_path / "metadata.sqlite3"))
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("u-default-001", "default@narravant.local", "Default User")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    document_id = _seed_saved_document(repository, storage)
    malformed, metadata = storage.read_structured_script(document_id, 1)
    malformed.pop("analysis")
    storage.write_structured_script(document_id, 1, malformed, if_generation_match=metadata.generation)

    with pytest.raises(ApiError) as caught:
        await get_document_detail(document_id, repository, storage, DEFAULT_USER)

    assert caught.value.status_code == 500
    assert caught.value.code == ApiErrorCode.DOCUMENT_CONTENT_MISSING


@pytest.mark.asyncio
async def test_only_owner_receives_mutation_capabilities_and_can_save(tmp_path) -> None:
    db = DatabaseManager(str(tmp_path / "metadata.sqlite3"))
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("u-default-001", "default@narravant.local", "Default User")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    document_id = _seed_saved_document(repository, storage)

    owner_detail = await get_document_detail(document_id, repository, storage, user=DEFAULT_USER)
    assert owner_detail.capabilities.can_edit is True
    assert owner_detail.capabilities.can_share is False
    assert owner_detail.capabilities.can_delete is True

    updated = await update_document(
        document_id,
        DocumentUpdateRequest(expected_version=1, title="Updated Title"),
        repository,
        storage,
        user=DEFAULT_USER,
        settings=EMOTION_ARC_SETTINGS,
    )
    assert updated.title == "Updated Title"

    with pytest.raises(ApiError) as caught:
        await update_document(
            "missing-doc-id",
            DocumentUpdateRequest(expected_version=1, title="Denied"),
            repository,
            storage,
            user=DEFAULT_USER,
            settings=EMOTION_ARC_SETTINGS,
        )
    assert caught.value.status_code == 404
    assert caught.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND


def test_consistency_audit_rejects_path_identity_mismatch_without_printing_body() -> None:
    payload = {
        "schema_version": 1,
        "document_id": "wrong-document",
        "version_id": 1,
        "source_fountain": "INT. ROOM - DAY",
        "scenes": [],
        "emotion_arc": {},
    }
    assert validate_payload(payload, "expected-document", 1, max_points=36) == "document_id does not match SQLite"


def test_consistency_audit_rejects_v1_payload_without_canonical_source_and_analysis() -> None:
    payload = {
        "schema_version": 1,
        "document_id": "expected-document",
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source_fountain": "INT. ROOM - DAY",
        "metadata": {"title": "Test"},
        "scenes": [],
        "emotion_arc": {},
    }
    assert validate_payload(payload, "expected-document", 1, max_points=36) == "source is malformed"


def test_consistency_audit_accepts_the_36_point_mapping_for_37_scenes() -> None:
    """The read-only audit must not mistake the canonical compressed arc for corruption."""
    source_fountain = "Title: Audit\n\nINT. ROOM - DAY #1#"
    payload = {
        "schema_version": 1,
        "document_id": "expected-document",
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source": {
            "filename": "audit.fountain",
            "media_type": "text/x-fountain",
            "sha256": sha256(source_fountain.encode("utf-8")).hexdigest(),
        },
        "source_fountain": source_fountain,
        "metadata": {"title": "Audit"},
        "scenes": [{"scene_number": number} for number in range(1, 38)],
        "analysis": {"status": "completed", "turning_points": [], "characters": []},
        "emotion_arc": {
            "valence": [4] * 36,
            "tension": [0] * 36,
            "characters": {},
            "scene_mapping": scene_mapping_payload(37, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }

    assert validate_payload(payload, "expected-document", 1, max_points=36) is None
