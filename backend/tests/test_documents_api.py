"""Saved-document API contracts after the Import-draft workflow replacement."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from narravant.api.dependencies import CurrentUser
from narravant.api.documents import (
    DocumentUpdateRequest,
    get_document_detail,
    get_document_version,
    list_document_versions,
    list_documents,
    update_document,
)
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.api.schemas import EmotionArcSchema
from narravant.core.valence_pattern import load_valence_patterns
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.storage.gcs import InMemoryScriptStorageClient

VALENCE_PATTERNS = load_valence_patterns(
    [
        {"id": "n", "name": "N-shaped curve", "description": "N", "control_values": [1, 7, 1, 7]},
        {"id": "reverse_n", "name": "Inverted N curve", "description": "rN", "control_values": [7, 1, 7, 1]},
        {"id": "v", "name": "V-shaped curve", "description": "V", "control_values": [7, 1, 7]},
        {"id": "reverse_v", "name": "Inverted V curve", "description": "rV", "control_values": [1, 7, 1]},
        {"id": "rise", "name": "Rising curve", "control_values": [1, 7]},
        {"id": "fall", "name": "Falling curve", "control_values": [7, 1]},
    ],
    value_min=1,
    value_max=7,
)
EMOTION_ARC_SETTINGS = SimpleNamespace(emotion_arc_max_points=36)


def _payload(document_id: str, owner_user_id: str, title: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": owner_user_id,
        "source": {
            "filename": f"{document_id}.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": "INT. ROOM - DAY #1#\n\nAction.",
        "metadata": {"title": title, "logline": "", "synopsis": "", "characters": []},
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "Action.",
                "dialogues": [],
            }
        ],
        "analysis": {"status": "not_requested", "turning_points": [], "characters": []},
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {},
            "scene_mapping": [
                {
                    "point_number": 1,
                    "start_scene_number": 1,
                    "end_scene_number": 1,
                    "representative_scene_number": 1,
                }
            ],
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }


def test_canonical_emotion_arc_schema_requires_source_scene_mapping() -> None:
    """Allowing completed arcs without a mapping would reintroduce legacy fallback data."""
    with pytest.raises(ValueError, match="scene_mapping"):
        EmotionArcSchema(
            valence=[4],
            tension=[0],
            characters={},
            valence_vector=[0.0] * 9 + [1.0],
        )


def _context() -> tuple[DocumentRepository, InMemoryScriptStorageClient, CurrentUser]:
    database = DatabaseManager(":memory:")
    database.init_schema()
    repository = DocumentRepository(database)
    owner = CurrentUser("owner-1", "owner@example.test")
    repository.ensure_user(owner.user_id, owner.email, "Owner")
    storage = InMemoryScriptStorageClient()
    _save_document(repository, storage, "doc-1", "Saved Document")
    return repository, storage, owner


def _save_document(
    repository: DocumentRepository,
    storage: InMemoryScriptStorageClient,
    document_id: str,
    title: str,
    *,
    vector: list[float] | None = None,
) -> None:
    payload = _payload(document_id, "owner-1", title)
    if vector is not None:
        payload["emotion_arc"]["valence_vector"] = vector
    stored = storage.write_structured_script(
        document_id,
        1,
        payload,
        if_generation_match=0,
    )
    storage.write_search_text(
        document_id,
        str(payload["source_fountain"]),
        if_generation_match=0,
    )
    repository.create_document(
        document_id,
        "owner-1",
        title,
        stored.uri,
        stored.generation,
        stored.sha256,
        vector,
    )


async def _list_documents(
    repository: DocumentRepository,
    storage: InMemoryScriptStorageClient,
    settings: SimpleNamespace,
    owner: CurrentUser,
    **overrides: object,
):
    return await list_documents(
        repository,
        storage,
        settings,
        owner,
        query=overrides.get("query"),
        limit=50,
        offset=0,
        sort=overrides.get("sort"),
        similar_to_arc_id=overrides.get("similar_to_arc_id"),
        arc_pattern_id=overrides.get("arc_pattern_id"),
    )


@pytest.mark.asyncio
async def test_saved_document_detail_contains_canonical_analysis_shape() -> None:
    repository, storage, owner = _context()

    detail = await get_document_detail("doc-1", repository, storage, owner)

    assert detail.document_id == "doc-1"
    assert detail.title == "Saved Document"
    assert detail.expected_version == 1
    assert detail.analysis.status == "not_requested"
    assert detail.emotion_arc.valence == [4]
    assert detail.capabilities.can_edit is True


@pytest.mark.asyncio
async def test_list_filters_and_server_side_sort_remain_saved_document_contracts() -> None:
    repository, storage, owner = _context()
    _save_document(repository, storage, "doc-2", "Comedy Movie")
    settings = SimpleNamespace(valence_patterns=VALENCE_PATTERNS)

    filtered = await _list_documents(
        repository,
        storage,
        settings,
        owner,
        query="Comedy",
        sort=["title:desc"],
    )
    assert [item.document_id for item in filtered.items] == ["doc-2"]
    assert filtered.total == 1

    with pytest.raises(HTTPException) as invalid_sort:
        await _list_documents(
            repository,
            storage,
            settings,
            owner,
            sort=["unknown:desc"],
        )
    assert invalid_sort.value.status_code == 422


@pytest.mark.asyncio
async def test_similarity_source_must_be_visible_and_have_an_arc() -> None:
    repository, storage, owner = _context()
    settings = SimpleNamespace(valence_patterns=VALENCE_PATTERNS)

    with pytest.raises(ApiError) as missing:
        await _list_documents(
            repository,
            storage,
            settings,
            owner,
            similar_to_arc_id="missing-document",
        )
    assert missing.value.status_code == 404
    assert missing.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND


@pytest.mark.asyncio
async def test_configured_valence_pattern_is_a_real_similarity_criterion() -> None:
    repository, storage, owner = _context()
    _save_document(
        repository,
        storage,
        "doc-2",
        "Pattern target",
        vector=[0.0] * 9 + [1.0],
    )
    settings = SimpleNamespace(valence_patterns=VALENCE_PATTERNS)

    response = await _list_documents(
        repository,
        storage,
        settings,
        owner,
        arc_pattern_id="n",
    )
    assert [item.document_id for item in response.items] == ["doc-2"]
    assert all(item.arc_distance is not None for item in response.items)

    with pytest.raises(HTTPException) as ambiguous:
        await _list_documents(
            repository,
            storage,
            settings,
            owner,
            similar_to_arc_id="doc-1",
            arc_pattern_id="n",
        )
    assert ambiguous.value.status_code == 422


@pytest.mark.asyncio
async def test_valence_search_filters_dissimilar_documents_by_threshold() -> None:
    from narravant.core.valence_vector import default_valence_vectorizer

    repository, storage, owner = _context()
    n_pattern = next(p for p in VALENCE_PATTERNS if p.pattern_id == "n")
    n_vector = default_valence_vectorizer.vectorize(n_pattern.control_values)
    opposite_vector = [-v for v in n_vector]

    _save_document(
        repository,
        storage,
        "doc-similar",
        "Similar Doc",
        vector=n_vector,
    )
    _save_document(
        repository,
        storage,
        "doc-dissimilar",
        "Dissimilar Doc",
        vector=opposite_vector,
    )

    settings = SimpleNamespace(
        valence_patterns=VALENCE_PATTERNS,
        valence_max_arc_distance=0.5,
    )

    pattern_response = await _list_documents(
        repository,
        storage,
        settings,
        owner,
        arc_pattern_id="n",
    )
    matching_ids = [item.document_id for item in pattern_response.items]
    assert "doc-similar" in matching_ids
    assert "doc-dissimilar" not in matching_ids

    similar_response = await _list_documents(
        repository,
        storage,
        settings,
        owner,
        similar_to_arc_id="doc-similar",
    )
    similar_ids = [item.document_id for item in similar_response.items]
    assert "doc-similar" in similar_ids
    assert "doc-dissimilar" not in similar_ids


@pytest.mark.asyncio
async def test_saved_document_update_uses_optimistic_locking() -> None:
    repository, storage, owner = _context()

    updated = await update_document(
        "doc-1",
        DocumentUpdateRequest(expected_version=1, title="Updated Title"),
        repository,
        storage,
        owner,
        EMOTION_ARC_SETTINGS,
    )
    # Title-only edits must NOT create a new content version (renaming is cheap),
    # but must still advance the optimistic-lock counter so stale writes conflict.
    assert updated.version_id == 1
    assert updated.title == "Updated Title"

    with pytest.raises(HTTPException) as stale:
        await update_document(
            "doc-1",
            DocumentUpdateRequest(expected_version=1, title="Conflicting Title"),
            repository,
            storage,
            owner,
            EMOTION_ARC_SETTINGS,
        )
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_character_set_edits_save_without_rewriting_orphaned_turning_point_history() -> None:
    repository, storage, owner = _context()
    document_id = "doc-character-history"
    payload = _payload(document_id, owner.user_id, "Character history")
    payload["analysis"] = {
        "status": "completed",
        "turning_points": [
            {
                "tp_number": 1,
                "label": "Opportunity",
                "availability": "identified",
                "scene_number": 1,
                "change": "Historical change",
                "reason": None,
                "involved_characters": [
                    {
                        "name": "Removed speaker",
                        "goal": "Old goal",
                        "conflict": "Old conflict",
                        "choice": "Old choice",
                        "action": "Old action",
                        "change": "Old character change",
                    }
                ],
            }
        ],
        "characters": [
            {"name": "Retained speaker", "related_turning_points": [1]},
            {"name": "Removed speaker", "related_turning_points": [1]},
        ],
    }
    payload["emotion_arc"]["characters"] = {"Retained speaker": [5], "Removed speaker": [3]}
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    storage.write_search_text(document_id, str(payload["source_fountain"]), if_generation_match=0)
    repository.create_document(
        document_id,
        owner.user_id,
        "Character history",
        stored.uri,
        stored.generation,
        stored.sha256,
    )

    edited = await update_document(
        document_id,
        DocumentUpdateRequest.model_validate(
            {
                "expected_version": 1,
                "base_version_id": 1,
                "analysis": {
                    "status": "completed",
                    "turning_points": payload["analysis"]["turning_points"],
                    "characters": [
                        {"name": "Retained speaker", "related_turning_points": [1]},
                        {"name": "Added speaker", "related_turning_points": []},
                    ],
                },
                "emotion_arc": {
                    **payload["emotion_arc"],
                    "characters": {"Retained speaker": [5], "Added speaker": [0]},
                },
            }
        ),
        repository,
        storage,
        owner,
        EMOTION_ARC_SETTINGS,
    )

    persisted, _ = storage.read_structured_script(document_id, edited.version_id)
    assert {character["name"] for character in persisted["analysis"]["characters"]} == {
        "Retained speaker",
        "Added speaker",
    }
    assert persisted["emotion_arc"]["characters"] == {"Retained speaker": [5], "Added speaker": [0]}
    assert persisted["analysis"]["turning_points"] == payload["analysis"]["turning_points"]
    assert edited.version_id == 2


@pytest.mark.asyncio
async def test_missing_saved_document_resources_use_document_not_found() -> None:
    repository, storage, owner = _context()

    with pytest.raises(ApiError) as missing_document:
        await list_document_versions("missing", repository, owner)
    assert missing_document.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND

    with pytest.raises(ApiError) as missing_version:
        await get_document_version("doc-1", 999, repository, storage, owner)
    assert missing_version.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND
