"""Completed emotional-arc API contract tests."""

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from narravant.api.dependencies import CurrentUser
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.services.vertex_analysis import CanonicalAnalysis
from narravant.storage.gcs import InMemoryScriptStorageClient


def test_pattern_catalog_returns_the_configured_names_in_configured_order() -> None:
    from narravant.api.emotion_arc import get_valence_patterns
    from narravant.core.valence_pattern import load_valence_patterns

    patterns = load_valence_patterns(
        [
            {"id": "n", "name": "Rise-fall-rise", "control_values": [1, 7, 1, 7]},
            {"id": "reverse_n", "name": "Fall-rise-fall", "control_values": [7, 1, 7, 1]},
            {"id": "v", "name": "Fall-rise", "control_values": [7, 1, 7]},
            {"id": "reverse_v", "name": "Rise-fall", "control_values": [1, 7, 1]},
            {"id": "rise", "name": "Rise", "control_values": [1, 7]},
            {"id": "fall", "name": "Fall", "control_values": [7, 1]},
        ],
        value_min=1,
        value_max=7,
    )

    response = get_valence_patterns(
        SimpleNamespace(valence_patterns=patterns),
        CurrentUser("owner-1", "owner@example.test"),
    )

    assert [(item.pattern_id, item.name) for item in response.items] == [
        ("n", "Rise-fall-rise"),
        ("reverse_n", "Fall-rise-fall"),
        ("v", "Fall-rise"),
        ("reverse_v", "Rise-fall"),
        ("rise", "Rise"),
        ("fall", "Fall"),
    ]


@pytest.mark.asyncio
async def test_pattern_similarity_returns_all_configured_patterns_for_completed_current_version() -> None:
    from narravant.api.dependencies import CurrentUser
    from narravant.api.emotion_arc import get_emotion_arc_similarities
    from narravant.core.valence_pattern import load_valence_patterns

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient()
    document_id = "doc-1"
    payload = {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source": {
            "filename": "story.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": "INT. ROOM - DAY",
        "metadata": {"title": "Test", "logline": "", "synopsis": "", "characters": []},
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "",
                "dialogues": [],
            }
        ],
        "analysis": {"status": "completed", "turning_points": [], "characters": []},
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    repo.create_document(
        document_id,
        "owner-1",
        "Test",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )
    patterns = load_valence_patterns(
        [
            {"id": "n", "name": "N", "control_values": [1, 7, 1, 7]},
            {"id": "reverse_n", "name": "逆N", "control_values": [7, 1, 7, 1]},
            {"id": "v", "name": "V", "control_values": [7, 1, 7]},
            {"id": "reverse_v", "name": "逆V", "control_values": [1, 7, 1]},
            {"id": "rise", "name": "上昇", "control_values": [1, 7]},
            {"id": "fall", "name": "下降", "control_values": [7, 1]},
        ],
        value_min=1,
        value_max=7,
    )

    response = await get_emotion_arc_similarities(
        document_id,
        CurrentUser("owner-1", "owner@example.test"),
        repo,
        storage,
        SimpleNamespace(valence_patterns=patterns),
    )

    assert {item.pattern_id for item in response.items} == {pattern.pattern_id for pattern in patterns}
    assert all(0.0 <= item.percentage <= 100.0 for item in response.items)


def _completed_analysis() -> CanonicalAnalysis:
    """Reanalysis用の完成分析スタブ（1シーン・TP5件）。"""
    labels = {
        1: "Opportunity",
        2: "Change of Plans",
        3: "Point of No Return",
        4: "Major Setback",
        5: "Climax",
    }
    return CanonicalAnalysis(
        metadata={
            "title": "Analyzer title",
            "logline": "検証",
            "synopsis": "あらすじ",
            "theme_setting": "主要なテーマ候補は選択。根拠は第1シーン",
        },
        emotion_arc={
            "valence": [7],
            "tension": [0],
            "characters": {"A": [7]},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
        characters=[
            {
                "name": "A",
                "external_goal": "外的目標",
                "internal_need": "内的欲求",
                "fear_or_cost": "恐れ",
                "obstacle": "障害",
                "choice": "選択",
                "agency": "主体性",
                "goal_to_outcome": "目標→結果",
                "related_turning_points": [3, 5],
            }
        ],
        turning_points=[
            {
                "tp_number": tp_number,
                "label": labels[tp_number],
                "availability": "identified",
                "scene_number": 1,
                "change": f"物語の変化{tp_number}",
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
            for tp_number in range(1, 6)
        ],
    )


def test_reanalysis_returns_an_unsaved_arc_draft_without_publishing_a_new_version() -> None:
    from narravant.core.valence_vector import default_valence_vectorizer
    from narravant.db.database import TaskRepository
    from narravant.services.reanalysis import ReanalysisService
    from narravant.services.tasks import TaskManager
    from narravant.services.vertex_analysis import CanonicalAnalysis

    class Analyzer:
        def convert_text_to_fountain(
            self,
            source_text: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
        ) -> str:
            return source_text

        def analyze(
            self,
            source_fountain: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
            source_filename: str | None = None,
            expected_characters: list[str] | None = None,
        ) -> CanonicalAnalysis:
            assert "INT. ROOM - DAY" in source_fountain
            return _completed_analysis()

    db = DatabaseManager(":memory:")
    db.init_schema()
    documents = DocumentRepository(db)
    documents.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient()
    tasks = TaskManager(TaskRepository(db))
    analyzer = Analyzer()
    document_id = "reanalysis-document"
    source_fountain = "Title: Original\n\nINT. ROOM - DAY #1#\n\nAction."
    initial = _completed_analysis()
    payload = {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source": {
            "filename": "draft.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": source_fountain,
        "metadata": initial.metadata,
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "Action.",
                "dialogues": [],
            }
        ],
        "analysis": {
            "status": "completed",
            "turning_points": initial.turning_points,
            "characters": initial.characters,
        },
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {"A": [4]},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    storage.write_search_text(document_id, source_fountain, if_generation_match=0)
    documents.create_document(
        document_id,
        "owner-1",
        "Original",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )
    before, before_metadata = storage.read_structured_script(document_id, 1)

    reanalysis = ReanalysisService(documents, storage, tasks, analyzer, emotion_arc_max_points=36)
    task_id = reanalysis.start("owner-1", documents.get_document(document_id))  # type: ignore[arg-type]
    reanalysis.run(task_id)

    task = tasks.repository.get(task_id)
    after, after_metadata = storage.read_structured_script(document_id, 1)
    assert task is not None and task["status"] == "completed"
    assert task["document_id"] == document_id
    assert after == before
    assert after_metadata.generation == before_metadata.generation
    assert documents.get_document(document_id)["current_version_id"] == 1
    assert documents.get_document(document_id)["version_id"] == 1
    with pytest.raises(FileNotFoundError):
        storage.read_structured_script(document_id, 2)
    completed = tasks.events_after(task_id, 0)[-1]
    assert completed.event_type == "completed"
    assert completed.payload == {
        "document_id": document_id,
        "version_id": 1,
        "reanalysis": {
            "emotion_arc": {
                "valence": [7],
                "tension": [0],
                "characters": {"A": [7]},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": default_valence_vectorizer.vectorize([7.0]),
            }
        },
    }

    conflicting_task_id = reanalysis.start("owner-1", documents.get_document(document_id))  # type: ignore[arg-type]
    documents.update_document(document_id, expected_version=1, title="Original (changed)")
    reanalysis.run(conflicting_task_id)

    conflict = tasks.events_after(conflicting_task_id, 0)[-1]
    assert conflict.event_type == "error"
    assert conflict.payload == {
        "code": "CONFLICT",
        "message": "文書が更新されたため再分析結果を適用できませんでした。",
        "retryable": True,
    }
    with pytest.raises(FileNotFoundError):
        storage.read_structured_script(document_id, 2)


def test_reanalysis_aligns_character_names_when_llm_shortens_or_alters_them() -> None:
    from narravant.core.valence_vector import default_valence_vectorizer
    from narravant.db.database import TaskRepository
    from narravant.services.reanalysis import ReanalysisService
    from narravant.services.tasks import TaskManager
    from narravant.services.vertex_analysis import CanonicalAnalysis

    class AbbreviatingAnalyzer:
        def convert_text_to_fountain(
            self,
            source_text: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
        ) -> str:
            return source_text

        def analyze(
            self,
            source_fountain: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
            source_filename: str | None = None,
            expected_characters: list[str] | None = None,
        ) -> CanonicalAnalysis:
            assert expected_characters == ["茨城暦（宇賀貞治）"]
            # LLM returned the shortened name "茨城暦"
            return CanonicalAnalysis(
                metadata={"title": "Test", "synopsis": "", "theme_setting": "根拠は第1シーン"},
                emotion_arc={
                    "valence": [5],
                    "tension": [1],
                    "characters": {"茨城暦": [5]},
                    "scene_mapping": scene_mapping_payload(1, 36),
                    "valence_vector": default_valence_vectorizer.vectorize([5.0]),
                },
                characters=[
                    {
                        "name": "茨城暦",
                        "external_goal": "",
                        "internal_need": "",
                        "fear_or_cost": "",
                        "obstacle": "",
                        "choice": "",
                        "agency": "",
                        "goal_to_outcome": "",
                        "related_turning_points": [1],
                    }
                ],
                turning_points=[
                    {
                        "tp_number": 1,
                        "label": "機会",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": "変化",
                        "involved_characters": [],
                    },
                    {"tp_number": 2, "label": "計画変更", "availability": "not_applicable", "reason": "該当なし"},
                    {"tp_number": 3, "label": "後戻り不能点", "availability": "not_applicable", "reason": "該当なし"},
                    {"tp_number": 4, "label": "大きな挫折", "availability": "not_applicable", "reason": "該当なし"},
                    {"tp_number": 5, "label": "クライマックス", "availability": "not_applicable", "reason": "該当なし"},
                ],
            )

    db = DatabaseManager(":memory:")
    db.init_schema()
    documents = DocumentRepository(db)
    documents.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient()
    tasks = TaskManager(TaskRepository(db))
    analyzer = AbbreviatingAnalyzer()
    document_id = "reanalysis-character-name"
    source_fountain = "Title: Original\n\nINT. ROOM - DAY #1#\n\nAction."
    payload = {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source": {
            "filename": "draft.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": source_fountain,
        "metadata": {"title": "Original", "synopsis": "", "theme_setting": "根拠は第1シーン"},
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "Action.",
                "dialogues": [],
            }
        ],
        "analysis": {
            "status": "completed",
            "turning_points": [
                {
                    "tp_number": 1,
                    "label": "機会",
                    "availability": "identified",
                    "scene_number": 1,
                    "change": "変化",
                    "involved_characters": [],
                },
                {"tp_number": 2, "label": "計画変更", "availability": "not_applicable", "reason": "該当なし"},
                {"tp_number": 3, "label": "後戻り不能点", "availability": "not_applicable", "reason": "該当なし"},
                {"tp_number": 4, "label": "大きな挫折", "availability": "not_applicable", "reason": "該当なし"},
                {"tp_number": 5, "label": "クライマックス", "availability": "not_applicable", "reason": "該当なし"},
            ],
            "characters": [
                {
                    "name": "茨城暦（宇賀貞治）",
                    "external_goal": "",
                    "internal_need": "",
                    "fear_or_cost": "",
                    "obstacle": "",
                    "choice": "",
                    "agency": "",
                    "goal_to_outcome": "",
                    "related_turning_points": [1],
                }
            ],
        },
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {"茨城暦（宇賀貞治）": [4]},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    documents.create_document(
        document_id,
        "owner-1",
        "Original",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )

    reanalysis = ReanalysisService(documents, storage, tasks, analyzer, emotion_arc_max_points=36)
    task_id = reanalysis.start("owner-1", documents.get_document(document_id))  # type: ignore[arg-type]
    reanalysis.run(task_id)

    task = tasks.repository.get(task_id)
    assert task is not None and task["status"] == "completed"
    completed = tasks.events_after(task_id, 0)[-1]
    assert completed.event_type == "completed"
    # The character arc must be mapped back to the canonical profile name "茨城暦（宇賀貞治）"
    assert "茨城暦（宇賀貞治）" in completed.payload["reanalysis"]["emotion_arc"]["characters"]
    assert completed.payload["reanalysis"]["emotion_arc"]["characters"]["茨城暦（宇賀貞治）"] == [5]
    assert "茨城暦" not in completed.payload["reanalysis"]["emotion_arc"]["characters"]


@pytest.mark.asyncio
async def test_reanalysis_endpoint_queues_an_owner_task() -> None:
    from narravant.api.dependencies import CurrentUser
    from narravant.api.emotion_arc import reanalyze_emotion_arc
    from narravant.db.database import TaskRepository
    from narravant.services.tasks import TaskManager
    from narravant.services.vertex_analysis import CanonicalAnalysis

    class Analyzer:
        def convert_text_to_fountain(
            self,
            source_text: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
        ) -> str:
            return source_text

        def analyze(
            self,
            source_fountain: str,
            on_progress,
            *,
            processing_deadline: float | None = None,
            source_filename: str | None = None,
            expected_characters: list[str] | None = None,
        ) -> CanonicalAnalysis:
            raise AssertionError("background worker must not run before the 202 response")

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient()
    document_id = "doc-queue"
    payload = {
        "schema_version": 1,
        "document_id": document_id,
        "version_id": 1,
        "owner_user_id": "owner-1",
        "source": {
            "filename": "story.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        "source_fountain": "INT. ROOM - DAY",
        "metadata": {"title": "Test", "logline": "", "synopsis": "", "characters": []},
        "scenes": [
            {
                "scene_number": 1,
                "heading": "INT. ROOM - DAY",
                "text": "",
                "dialogues": [],
            }
        ],
        "analysis": {"status": "completed", "turning_points": [], "characters": []},
        "emotion_arc": {
            "valence": [4],
            "tension": [0],
            "characters": {},
            "scene_mapping": scene_mapping_payload(1, 36),
            "valence_vector": [0.0] * 9 + [1.0],
        },
    }
    stored = storage.write_structured_script(document_id, 1, payload, if_generation_match=0)
    repo.create_document(
        document_id,
        "owner-1",
        "Test",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )
    tasks = TaskManager(TaskRepository(db))
    background = BackgroundTasks()

    accepted = await reanalyze_emotion_arc(
        document_id,
        background,
        CurrentUser("owner-1", "owner@example.test"),
        repo,
        storage,
        tasks,
        Analyzer(),
        SimpleNamespace(emotion_arc_max_points=36),
    )

    queued = tasks.repository.get(accepted.task_id)
    assert accepted.task_type == "emotion_arc_reanalysis"
    assert queued is not None and queued["status"] == "queued"
    assert len(background.tasks) == 1

    # Missing document gets 404
    with pytest.raises(ApiError) as caught_missing:
        await reanalyze_emotion_arc(
            "missing-doc",
            BackgroundTasks(),
            CurrentUser("owner-1", "owner@example.test"),
            repo,
            storage,
            tasks,
            Analyzer(),
            SimpleNamespace(emotion_arc_max_points=36),
        )
    assert caught_missing.value.status_code == 404
    assert caught_missing.value.code == ApiErrorCode.DOCUMENT_NOT_FOUND
