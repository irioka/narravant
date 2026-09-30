"""Tests for Task 8 (B1): Voice schemas, narrator, voice_assignments, and roundtrip preservation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from narravant.api.dependencies import CurrentUser
from narravant.api.documents import (
    get_document_detail,
    update_document,
)
from narravant.api.schemas import (
    DocumentUpdateRequest,
    ImportDraftSaveRequest,
    NarratorSchema,
    NativeExchangeEnvelope,
    VoiceAssignmentSchema,
)
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.services.ingestion import (
    ImportDraft,
    _apply_import_draft_edits,
    draft_response,
    native_exchange_to_v1,
    validate_import,
)
from narravant.storage.gcs import InMemoryScriptStorageClient


def _make_native_payload_with_voices(
    *,
    extra_field_in_narrator: bool = False,
    extra_field_in_assignment: bool = False,
) -> dict:
    narrator_data: dict = {"voice_traits": "落ち着いた中低音の語り手"}
    if extra_field_in_narrator:
        narrator_data["unexpected_trait"] = "hack"

    assignment_data: dict = {
        "speaker": "A",
        "voice_id": "voices/voice-a-123",
        "voice_traits": "快活な少年声",
    }
    if extra_field_in_assignment:
        assignment_data["unexpected_id"] = "hack"

    return {
        "format": "narravant-native",
        "format_version": 1,
        "exported_at": "2026-09-28T12:00:00Z",
        "document": {
            "title": "Voice Test Work",
            "source_fountain": "Title: Voice Test Work\n\nINT. ROOM - DAY #1#\n\n@A\nこんにちは。\n",
            "metadata": {
                "title": "Voice Test Work",
                "logline": "声情報の検証作品",
                "synopsis": "あらすじ",
                "theme_setting": "現代劇",
            },
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": i,
                        "availability": "identified",
                        "scene_number": 1,
                        "change": f"変化 {i}",
                        "involved_characters": [
                            {
                                "name": "A",
                                "goal": "g",
                                "conflict": "c",
                                "choice": "q",
                                "action": "a",
                                "change": "d",
                            }
                        ],
                        "reason": None,
                    }
                    for i in range(1, 6)
                ],
                "characters": [
                    {
                        "name": "A",
                        "external_goal": "Goal",
                        "internal_need": "Need",
                        "fear_or_cost": "Cost",
                        "obstacle": "Obstacle",
                        "choice": "Choice",
                        "agency": "Agency",
                        "goal_to_outcome": "Outcome",
                        "related_turning_points": [1],
                        "voice_traits": "快活な少年声",
                    }
                ],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {"A": [4]},
            },
            "narrator": narrator_data,
            "voice_assignments": [assignment_data],
        },
    }


def test_native_schema_validates_and_forbids_extra_fields() -> None:
    """Native JSON schema permits narrator, voice_assignments and forbids unexpected fields."""
    valid_payload = _make_native_payload_with_voices()
    envelope = NativeExchangeEnvelope.model_validate(valid_payload)
    assert envelope.document.narrator.voice_traits == "落ち着いた中低音の語り手"
    assert len(envelope.document.voice_assignments) == 1
    assert envelope.document.voice_assignments[0].speaker == "A"
    assert envelope.document.voice_assignments[0].voice_id == "voices/voice-a-123"
    assert envelope.document.voice_assignments[0].voice_traits == "快活な少年声"
    assert envelope.document.analysis.characters[0].voice_traits == "快活な少年声"

    # Reject unexpected field in narrator
    invalid_narrator = _make_native_payload_with_voices(extra_field_in_narrator=True)
    with pytest.raises(ValidationError):
        NativeExchangeEnvelope.model_validate(invalid_narrator)

    # Reject unexpected field in voice_assignment
    invalid_assignment = _make_native_payload_with_voices(extra_field_in_assignment=True)
    with pytest.raises(ValidationError):
        NativeExchangeEnvelope.model_validate(invalid_assignment)


@pytest.mark.asyncio
async def test_import_save_get_put_roundtrip_preserves_voice_data() -> None:
    """Full roundtrip: native import -> save -> GET -> PUT -> re-read preserves narrator and voice assignments."""
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    storage = InMemoryScriptStorageClient()
    user = CurrentUser(user_id="user_test", email="test@example.com", display_name="Tester")
    repo.ensure_user(user.user_id, user.email, user.display_name)

    # 1. Native JSON import
    payload_dict = _make_native_payload_with_voices()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    imported = validate_import(
        "test.json",
        payload_bytes,
        10 * 1024 * 1024,
        pdf_max_pages=50,
        fdx_max_depth=30,
        txt_minimum_confidence=0.8,
    )

    doc_id = "doc-voice-test"
    structured = native_exchange_to_v1(
        imported.text,
        doc_id,
        user.user_id,
        imported,
        max_points=36,
    )

    # Verify structured dict has narrator and voice_assignments
    assert structured["narrator"] == {"voice_traits": "落ち着いた中低音の語り手"}
    assert structured["voice_assignments"] == [
        {"speaker": "A", "voice_id": "voices/voice-a-123", "voice_traits": "快活な少年声"}
    ]

    # Verify draft_response includes them
    draft = ImportDraft(
        task_id="task-1",
        owner_user_id=user.user_id,
        document_id=doc_id,
        expected_version=None,
        structured=structured,
        expires_at=9999999999.0,
    )
    draft_res = draft_response(draft)
    assert draft_res.narrator.voice_traits == "落ち着いた中低音の語り手"
    assert len(draft_res.voice_assignments) == 1
    assert draft_res.voice_assignments[0].speaker == "A"
    assert draft_res.voice_assignments[0].voice_id == "voices/voice-a-123"

    # 2. Save draft via ImportDraftSaveRequest
    save_request = ImportDraftSaveRequest(
        title=draft_res.title,
        fountain_text=draft_res.source_fountain,
        metadata=draft_res.metadata,
        analysis=draft_res.analysis,
        emotion_arc=draft_res.emotion_arc,
        narrator=draft_res.narrator,
        voice_assignments=draft_res.voice_assignments,
    )
    edited_structured = _apply_import_draft_edits(draft.structured, save_request, max_points=36)
    assert edited_structured["narrator"] == {"voice_traits": "落ち着いた中低音の語り手"}
    assert edited_structured["voice_assignments"] == [
        {"speaker": "A", "voice_id": "voices/voice-a-123", "voice_traits": "快活な少年声"}
    ]

    stored = storage.write_structured_script(doc_id, 1, edited_structured, if_generation_match=0)
    storage.write_search_text(
        doc_id,
        str(edited_structured["source_fountain"]),
        if_generation_match=0,
    )
    repo.create_document(
        doc_id,
        user.user_id,
        save_request.title,
        stored.uri,
        stored.generation,
        stored.sha256,
        valence_vector=edited_structured["emotion_arc"]["valence_vector"],
    )

    # 3. GET document detail
    detail = await get_document_detail(doc_id, repo, storage, user=user)
    assert detail.narrator.voice_traits == "落ち着いた中低音の語り手"
    assert len(detail.voice_assignments) == 1
    assert detail.voice_assignments[0].speaker == "A"
    assert detail.voice_assignments[0].voice_id == "voices/voice-a-123"
    assert detail.voice_assignments[0].voice_traits == "快活な少年声"

    # 4. PUT document update (modify narrator voice_traits and add a voice assignment)
    updated_narrator = NarratorSchema(voice_traits="深みのあるバリトン")
    updated_assignments = [
        VoiceAssignmentSchema(speaker="A", voice_id="voices/voice-a-new", voice_traits="大人びた青年声"),
        VoiceAssignmentSchema(
            speaker="ナレーター",
            voice_id="voices/voice-narrator-1",
            voice_traits="深みのあるバリトン",
        ),
    ]
    update_req = DocumentUpdateRequest(
        expected_version=1,
        narrator=updated_narrator,
        voice_assignments=updated_assignments,
    )

    dummy_settings = SimpleNamespace(
        valence_patterns=[],
        valence_max_arc_distance=None,
        emotion_arc_max_points=36,
    )
    updated_detail = await update_document(
        doc_id,
        update_req,
        repo,
        storage,
        user=user,
        settings=dummy_settings,
    )
    assert updated_detail.narrator.voice_traits == "深みのあるバリトン"
    assert len(updated_detail.voice_assignments) == 2
    assert updated_detail.voice_assignments[0].speaker == "A"
    assert updated_detail.voice_assignments[0].voice_id == "voices/voice-a-new"
    assert updated_detail.voice_assignments[1].speaker == "ナレーター"

    # 5. Re-read to confirm persistence in storage
    reread_detail = await get_document_detail(doc_id, repo, storage, user=user)
    assert reread_detail.narrator.voice_traits == "深みのあるバリトン"
    assert len(reread_detail.voice_assignments) == 2
    assert reread_detail.voice_assignments[0].voice_id == "voices/voice-a-new"
