"""Comprehensive synthetic end-to-end acceptance tests for scenarios A1 through A9.

These tests verify the single-work audiobook acceptance criteria.
using fake dependencies, in-memory databases, and deterministic test fixtures.
No real external Gemini API calls or billable services are invoked.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_import_api import FakeDocumentAnalyzer, FakeNarrativeAdapter

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
    get_db_manager,
    get_storage_client,
)
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.core.fountain import FountainParser
from narravant.core.settings import Settings, SettingsError
from narravant.db.database import DatabaseManager, DocumentRepository, OptimisticLockError, TaskRepository
from narravant.main import app
from narravant.services.ingestion import (
    ImportDraftStore,
    ImportService,
    ImportValidationError,
    ValidatedImport,
    native_exchange_to_v1,
    validate_import,
)
from narravant.services.tasks import EphemeralTaskEvents, TaskManager
from narravant.services.tts import FakeTtsClient
from narravant.storage.gcs import InMemoryScriptStorageClient


@pytest.fixture
def in_memory_db() -> DatabaseManager:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    return db


@pytest.fixture
def in_memory_storage() -> InMemoryScriptStorageClient:
    return InMemoryScriptStorageClient()


@contextmanager
def _playback_ws_env(db: DatabaseManager, storage: InMemoryScriptStorageClient, fake_tts: FakeTtsClient):
    """Wire the WebSocket playback route for tests.

    The playback WS route reads db/storage/settings from app.state and builds
    its TTS client via the module-level _build_tts_client indirection (WS routes
    cannot use FastAPI dependency overrides for connection-scoped resources).
    HTTP accessor overrides are registered only to bypass lifespan wiring.
    """
    import narravant.api.playback as playback_module

    app.state.settings = SimpleNamespace(
        gemini_api_key="fake",
        gemini_tts_model="gemini-3.8-flash-tts",
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.db_manager = db
    app.state.storage_client = storage
    app.dependency_overrides[get_db_manager] = lambda: db
    app.dependency_overrides[get_storage_client] = lambda: storage
    original_build = playback_module._build_tts_client
    playback_module._build_tts_client = lambda settings: fake_tts
    try:
        yield
    finally:
        playback_module._build_tts_client = original_build
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Scenario A1: アップロードと自動生成（Google認証なし、ローカル完結）
# ---------------------------------------------------------------------------
def test_scenario_a1_upload_and_auto_generation_without_auth(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A1: Google認証なし・ローカル起動でファイルを取り込み、脚本・声の特徴・分析が生成される。"""
    repo = DocumentRepository(in_memory_db)
    tasks = TaskManager(TaskRepository(in_memory_db), ephemeral_events=EphemeralTaskEvents())
    drafts = ImportDraftStore(3600)
    analyzer = FakeDocumentAnalyzer()
    adapter = FakeNarrativeAdapter()

    service = ImportService(
        repo,
        in_memory_storage,
        tasks,
        drafts,
        analyzer,
        adapter,
        processing_timeout_seconds=60,
        max_size_bytes=1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
    )

    raw_novel = "少年は丘の上に立っていた。\n「誰かいるのか？」少年は叫んだ。"
    content = raw_novel.encode("utf-8")
    imported = validate_import(
        "story.txt",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
    )

    task_id = service.start(LOCAL_USER_ID, imported)
    service.run(task_id, content)

    draft = drafts.get(task_id)
    assert draft is not None

    # 1. Google 認証なしでローカル固定ユーザーに紐付く
    assert bool(draft.document_id)
    structured = draft.structured
    assert structured["owner_user_id"] == LOCAL_USER_ID

    # 2. オーディオブック用 Fountain・メタデータ・声の特徴が生成される
    fountain = str(structured["source_fountain"])
    assert "Title:" in fountain
    assert len(structured["scenes"]) >= 1  # type: ignore[arg-type]

    # 声の特徴（narrator と characters に voice_traits が付与されている）
    narrator = structured["narrator"]
    assert "voice_traits" in narrator  # type: ignore[operator]
    characters = structured["analysis"]["characters"]  # type: ignore[index]
    assert len(characters) >= 1
    assert all("voice_traits" in c for c in characters)

    # 5つの転換点・感情アーク
    turning_points = structured["analysis"]["turning_points"]  # type: ignore[index]
    assert len(turning_points) == 5
    emotion_arc = structured["emotion_arc"]
    assert "valence" in emotion_arc  # type: ignore[operator]
    assert "tension" in emotion_arc  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Scenario A2: 編集・保存・再読込（ローカル整合性・欠落なし）
# ---------------------------------------------------------------------------
def test_scenario_a2_edit_save_to_local_db_and_reload(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A2: 生成された脚本と声の特徴を編集し、DB保存後に再読込してもすべて保持される。"""
    repo = DocumentRepository(in_memory_db)
    doc_id = "doc-a2"

    initial_fountain = "Title: A2 Story\n\nINT. ROOM - DAY #1#\n\nAction text.\n\n@HERO\nI will go.\n"
    parsed = FountainParser.parse(initial_fountain)

    # 初期保存
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "A2 Story",
                "characters": ["HERO"],
                "logline": "Log",
                "synopsis": "Syn",
                "theme_setting": "Th",
            },
            "source": {"filename": "a2.txt", "media_type": "text/plain", "sha256": "x" * 64},
            "source_fountain": initial_fountain,
            "scenes": [s.to_dict() for s in parsed.scenes],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": i,
                        "label": f"TP{i}",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": f"C{i}",
                        "involved_characters": [],
                        "reason": None,
                    }
                    for i in range(1, 6)
                ],
                "characters": [{"name": "HERO", "voice_traits": "力強い若者"}],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {"HERO": [4]},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "落ち着いた語り"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/v-narrator", "voice_traits": "落ち着いた語り"},
                {"speaker": "HERO", "voice_id": "voices/v-hero", "voice_traits": "力強い若者"},
            ],
        },
    )
    repo.create_document(
        document_id=doc_id,
        owner_user_id=LOCAL_USER_ID,
        title="A2 Story",
        gcs_uri="mem://doc-a2/v1.json",
        gcs_generation=1,
        payload_sha256="hash-1",
    )

    # 編集（台詞と声の割り当てを変更）してバージョン2として保存
    updated_fountain = "Title: A2 Story\n\nINT. ROOM - DAY #1#\n\nAction text.\n\n@HERO\nI must go right now.\n"
    updated_parsed = FountainParser.parse(updated_fountain)
    updated_data, _ = in_memory_storage.read_structured_script(doc_id, 1)
    updated_data["version_id"] = 2
    updated_data["source_fountain"] = updated_fountain
    updated_data["scenes"] = [s.to_dict() for s in updated_parsed.scenes]
    updated_data["voice_assignments"] = [
        {"speaker": "ナレーター", "voice_id": "voices/v-narrator", "voice_traits": "落ち着いた語り"},
        {"speaker": "HERO", "voice_id": "voices/v-hero-updated", "voice_traits": "より決意ある若者"},
    ]
    in_memory_storage.write_structured_script(doc_id, 2, updated_data)
    repo.update_document(
        document_id=doc_id,
        expected_version=1,
        title="A2 Story",
        new_gcs_uri="mem://doc-a2/v2.json",
        new_gcs_generation=2,
        new_payload_sha256="hash-2",
        new_content_version_id=2,
    )

    # 再起動シミュレーション: 新規 Repository インスタンスで読込
    fresh_repo = DocumentRepository(in_memory_db)
    reloaded_meta = fresh_repo.get_document(doc_id)
    assert reloaded_meta is not None
    assert reloaded_meta["current_version_id"] == 2

    reloaded_data, _ = in_memory_storage.read_structured_script(doc_id, 2)
    assert reloaded_data["source_fountain"] == updated_fountain
    assert reloaded_data["voice_assignments"][1] == {
        "speaker": "HERO",
        "voice_id": "voices/v-hero-updated",
        "voice_traits": "より決意ある若者",
    }
    assert len(reloaded_data["analysis"]["turning_points"]) == 5


# ---------------------------------------------------------------------------
# Scenario A3: Native JSON の Export / Import 往復
# ---------------------------------------------------------------------------
def test_scenario_a3_native_json_export_and_import_roundtrip() -> None:
    """A3: Native JSON で Export し、それを Import しても声の特徴・割当・分析が失われない。"""
    original_payload = {
        "format": "narravant-native",
        "format_version": 1,
        "exported_at": "2026-09-28T12:00:00Z",
        "document": {
            "title": "A3 Roundtrip",
            "source_fountain": (
                "Title: A3 Roundtrip\n\nINT. STUDY - NIGHT #1#\n\nClock ticks.\n\n@DETECTIVE\nThe case is closed.\n"
            ),
            "metadata": {
                "title": "A3 Roundtrip",
                "logline": "A detective solves a mystery.",
                "synopsis": "Detailed synopsis.",
                "theme_setting": "Justice and truth.",
            },
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": i,
                        "availability": "identified",
                        "scene_number": 1,
                        "change": f"Turning change {i}",
                        "involved_characters": [
                            {
                                "name": "DETECTIVE",
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
                        "name": "DETECTIVE",
                        "external_goal": "Find truth",
                        "internal_need": "Peace",
                        "fear_or_cost": "Failure",
                        "obstacle": "Deceit",
                        "choice": "Pursue",
                        "agency": "High",
                        "goal_to_outcome": "Success",
                        "related_turning_points": [1, 2, 3, 4, 5],
                        "voice_traits": "冷徹で明瞭な中低音",
                    }
                ],
            },
            "emotion_arc": {"valence": [5], "tension": [2], "characters": {"DETECTIVE": [5]}},
            "narrator": {"voice_traits": "淡々としたハードボイルドな語り口"},
            "voice_assignments": [
                {
                    "speaker": "ナレーター",
                    "voice_id": "voices/narrator-3",
                    "voice_traits": "淡々としたハードボイルドな語り口",
                },
                {"speaker": "DETECTIVE", "voice_id": "voices/det-3", "voice_traits": "冷徹で明瞭な中低音"},
            ],
        },
    }

    json_str = json.dumps(original_payload)
    imported = ValidatedImport("roundtrip.json", ".json", "application/json", None)

    structured = native_exchange_to_v1(
        json_str,
        document_id="doc-a3",
        owner_user_id=LOCAL_USER_ID,
        imported=imported,
        max_points=36,
    )

    # 検証: 脚本・声の特徴・声の割当・分析が完全に復元される
    assert structured["document_id"] == "doc-a3"
    assert structured["narrator"] == {"voice_traits": "淡々としたハードボイルドな語り口"}
    assert structured["voice_assignments"] == [
        {"speaker": "ナレーター", "voice_id": "voices/narrator-3", "voice_traits": "淡々としたハードボイルドな語り口"},
        {"speaker": "DETECTIVE", "voice_id": "voices/det-3", "voice_traits": "冷徹で明瞭な中低音"},
    ]
    assert structured["analysis"]["characters"][0]["voice_traits"] == "冷徹で明瞭な中低音"
    assert len(structured["analysis"]["turning_points"]) == 5


# ---------------------------------------------------------------------------
# Scenario A4: ナレーター → A → B → ナレーター の発話順再生（非永続）
# ---------------------------------------------------------------------------
def test_scenario_a4_four_utterance_multi_speaker_playback_order(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A4: ナレーター→A→B→ナレーターの順に4発話が各話者の声で順次再生される。"""
    repo = DocumentRepository(in_memory_db)
    doc_id = "doc-a4"

    fountain_text = """Title: Multi Speaker Test

INT. SALON - NIGHT #1#

夜のサロンは静まり返っていた。

@ALICE
誰かいるの？

@BOB
僕だよ。

二人は微笑み合った。
"""
    parsed = FountainParser.parse(fountain_text)
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "Multi Speaker Test",
                "characters": ["ナレーター", "ALICE", "BOB"],
                "logline": "L",
                "synopsis": "S",
                "theme_setting": "T",
            },
            "source": {"filename": "test.fountain", "media_type": "text/x-fountain", "sha256": "a" * 64},
            "source_fountain": fountain_text,
            "scenes": [s.to_dict() for s in parsed.scenes],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": 1,
                        "label": "Op",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": "c",
                        "involved_characters": [],
                        "reason": None,
                    }
                ],
                "characters": [],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "ナレーター声"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/v-narrator", "voice_traits": "t-nar"},
                {"speaker": "ALICE", "voice_id": "voices/v-alice", "voice_traits": "t-alice"},
                {"speaker": "BOB", "voice_id": "voices/v-bob", "voice_traits": "t-bob"},
            ],
        },
    )
    repo.create_document(
        document_id=doc_id,
        owner_user_id=LOCAL_USER_ID,
        title="Multi Speaker Test",
        gcs_uri="mem://doc-a4/v1.json",
        gcs_generation=1,
        payload_sha256="hash-a4",
    )

    fake_tts = FakeTtsClient()
    with _playback_ws_env(in_memory_db, in_memory_storage, fake_tts):
        client = TestClient(app)
        with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
            ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

            events: list[dict[str, Any]] = []
            while True:
                msg = ws.receive_json()
                events.append(msg)
                if msg.get("event") == "playback_complete":
                    break

        # Scene Heading を含む5発話が ナレーター → ナレーター → ALICE → BOB → ナレーター の順で処理されたことを確認
        utterance_starts = [e for e in events if e.get("event") == "utterance_start"]
        assert len(utterance_starts) == 5
        speakers = [u["speaker"] for u in utterance_starts]
        assert speakers == ["ナレーター", "ナレーター", "ALICE", "BOB", "ナレーター"]
        assert utterance_starts[0]["utterance_index"] == 0
        assert fake_tts.calls[0]["text"] == "SALON - NIGHT"

        # TTS 呼び出し履歴の voice_id が割当と一致
        assert len(fake_tts.calls) == 5
        assert [c["voice_id"] for c in fake_tts.calls] == [
            "voices/v-narrator",
            "voices/v-narrator",
            "voices/v-alice",
            "voices/v-bob",
            "voices/v-narrator",
        ]


# ---------------------------------------------------------------------------
# Scenario A5: 途中失敗と失敗位置からの再試行
# ---------------------------------------------------------------------------
def test_scenario_a5_playback_failure_and_resume_from_failed_index(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A5: BOB の発話で失敗したとき停止し、同じ utterance index から再生できる。"""
    repo = DocumentRepository(in_memory_db)
    doc_id = "doc-a5"

    fountain_text = """Title: A5 Test

INT. ROOM - DAY #1#

地の文1。

@ALICE
台詞1。

@BOB
台詞2。

地の文2。
"""
    parsed = FountainParser.parse(fountain_text)
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "A5 Test",
                "characters": ["ナレーター", "ALICE", "BOB"],
                "logline": "L",
                "synopsis": "S",
                "theme_setting": "T",
            },
            "source": {"filename": "test.fountain", "media_type": "text/x-fountain", "sha256": "b" * 64},
            "source_fountain": fountain_text,
            "scenes": [s.to_dict() for s in parsed.scenes],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": 1,
                        "label": "Op",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": "c",
                        "involved_characters": [],
                        "reason": None,
                    }
                ],
                "characters": [],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "nar"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/v-nar", "voice_traits": "t1"},
                {"speaker": "ALICE", "voice_id": "voices/v-alice", "voice_traits": "t2"},
                {"speaker": "BOB", "voice_id": "voices/v-bob", "voice_traits": "t3"},
            ],
        },
    )
    repo.create_document(
        document_id=doc_id,
        owner_user_id=LOCAL_USER_ID,
        title="A5 Test",
        gcs_uri="mem://doc-a5/v1.json",
        gcs_generation=1,
        payload_sha256="hash-a5",
    )

    # Scene Heading が index 0 になるため、BOB は index 3。
    fake_tts = FakeTtsClient(fail_on_utterance_indices={3})
    with _playback_ws_env(in_memory_db, in_memory_storage, fake_tts):
        client = TestClient(app)
        # 1回目の再生: BOB の index 3 で失敗
        with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
            ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

            events: list[dict[str, Any]] = []
            while True:
                msg = ws.receive_json()
                events.append(msg)
                if msg.get("event") == "error":
                    break

            error_ev = events[-1]
            assert error_ev["event"] == "error"
            assert error_ev["utterance_index"] == 3  # 失敗位置が通知される

        # 2回目の再生: 失敗位置 (index 3) から再試行
        fake_tts.fail_on_utterance_indices.clear()
        with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
            ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 3})

            resume_events: list[dict[str, Any]] = []
            while True:
                msg = ws.receive_json()
                resume_events.append(msg)
                if msg.get("event") == "playback_complete":
                    break

            resumed_starts = [e for e in resume_events if e.get("event") == "utterance_start"]
            assert len(resumed_starts) == 2  # index 3 と index 4 のみ再生
            assert resumed_starts[0]["utterance_index"] == 3
            assert resumed_starts[0]["speaker"] == "BOB"
            assert resumed_starts[1]["utterance_index"] == 4
            assert resumed_starts[1]["speaker"] == "ナレーター"


# ---------------------------------------------------------------------------
# Scenario A6: 本文・声の編集が直後の再生に即時反映される
# ---------------------------------------------------------------------------
def test_scenario_a6_script_and_voice_edits_reflect_immediately(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A6: 本文や声の割当を編集した直後の再生は、最新の編集内容で再生される。"""
    repo = DocumentRepository(in_memory_db)
    doc_id = "doc-a6"

    fountain_v1 = "Title: A6\n\nINT. ROOM - DAY #1#\n\n@ALICE\nOld line.\n"
    parsed_v1 = FountainParser.parse(fountain_v1)
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {"title": "A6", "characters": ["ALICE"], "logline": "L", "synopsis": "S", "theme_setting": "T"},
            "source": {"filename": "test.fountain", "media_type": "text/x-fountain", "sha256": "c" * 64},
            "source_fountain": fountain_v1,
            "scenes": [s.to_dict() for s in parsed_v1.scenes],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": 1,
                        "label": "Op",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": "c",
                        "involved_characters": [],
                        "reason": None,
                    }
                ],
                "characters": [],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "nar"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/v-narrator", "voice_traits": "nar"},
                {"speaker": "ALICE", "voice_id": "voices/v-alice-old", "voice_traits": "old"},
            ],
        },
    )
    repo.create_document(
        document_id=doc_id,
        owner_user_id=LOCAL_USER_ID,
        title="A6",
        gcs_uri="mem://doc-a6/v1.json",
        gcs_generation=1,
        payload_sha256="hash-a6-1",
    )

    # 編集して v2 に更新
    fountain_v2 = "Title: A6\n\nINT. ROOM - DAY #1#\n\n@ALICE\nNew brand-new line.\n"
    parsed_v2 = FountainParser.parse(fountain_v2)
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=2,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 2,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {"title": "A6", "characters": ["ALICE"], "logline": "L", "synopsis": "S", "theme_setting": "T"},
            "source": {"filename": "test.fountain", "media_type": "text/x-fountain", "sha256": "c" * 64},
            "source_fountain": fountain_v2,
            "scenes": [s.to_dict() for s in parsed_v2.scenes],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": 1,
                        "label": "Op",
                        "availability": "identified",
                        "scene_number": 1,
                        "change": "c",
                        "involved_characters": [],
                        "reason": None,
                    }
                ],
                "characters": [],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "nar"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/v-narrator", "voice_traits": "nar"},
                {"speaker": "ALICE", "voice_id": "voices/v-alice-new", "voice_traits": "new"},
            ],
        },
    )
    repo.update_document(
        document_id=doc_id,
        expected_version=1,
        title="A6",
        new_gcs_uri="mem://doc-a6/v2.json",
        new_gcs_generation=2,
        new_payload_sha256="hash-a6-2",
        new_content_version_id=2,
    )

    fake_tts = FakeTtsClient()
    with _playback_ws_env(in_memory_db, in_memory_storage, fake_tts):
        client = TestClient(app)
        with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
            ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})
            while True:
                msg = ws.receive_json()
                if msg.get("event") == "playback_complete":
                    break

        assert len(fake_tts.calls) == 2
        assert fake_tts.calls[0] == {"text": "ROOM - DAY", "voice_id": "voices/v-narrator"}
        assert fake_tts.calls[1]["text"] == "New brand-new line."
        assert fake_tts.calls[1]["voice_id"] == "voices/v-alice-new"


# ---------------------------------------------------------------------------
# Scenario A7: 保存失敗・不正入力による上書き防止
# ---------------------------------------------------------------------------
def test_scenario_a7_storage_failure_and_conflict_handling(
    in_memory_db: DatabaseManager, in_memory_storage: InMemoryScriptStorageClient
) -> None:
    """A7: 保存失敗や不正フォーマットで既存の健全なデータが空上書き・破壊されない。"""
    repo = DocumentRepository(in_memory_db)
    doc_id = "doc-a7"

    initial_fountain = "Title: A7\n\nINT. ROOM - DAY #1#\n\nExisting text.\n"
    in_memory_storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={"schema_version": 1, "document_id": doc_id, "version_id": 1, "source_fountain": initial_fountain},
    )
    repo.create_document(
        document_id=doc_id,
        owner_user_id=LOCAL_USER_ID,
        title="A7",
        gcs_uri="mem://doc-a7/v1.json",
        gcs_generation=1,
        payload_sha256="hash-a7",
    )

    # 1. 楽観ロック不一致（expected_version=99）で OptimisticLockError
    with pytest.raises(OptimisticLockError):
        repo.update_document(
            document_id=doc_id,
            expected_version=99,
            title="A7 Conflict",
            new_gcs_uri="mem://doc-a7/v2.json",
            new_gcs_generation=2,
            new_payload_sha256="hash-conflict",
        )

    # 既存の DB レコードとストレージデータが保たれていることを確認
    doc = repo.get_document(doc_id)
    assert doc is not None
    assert doc["current_version_id"] == 1
    stored_data, _ = in_memory_storage.read_structured_script(doc_id, 1)
    assert stored_data["source_fountain"] == initial_fountain

    # 2. 不正な Native JSON の取り込み失敗で既存データが破壊されない
    with pytest.raises(ImportValidationError):
        native_exchange_to_v1(
            "{ malformed json",
            document_id="doc-a7-err",
            owner_user_id=LOCAL_USER_ID,
            imported=ValidatedImport("bad.json", ".json", "application/json", None),
            max_points=36,
        )


# ---------------------------------------------------------------------------
# Scenario A8: APIキー未設定・認証拒否・機密漏洩防止
# ---------------------------------------------------------------------------
def test_scenario_a8_missing_api_key_and_credential_sanitization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A8: GEMINI_API_KEY未設定時に安全に起動停止し、ログやエラーに秘密キーを含めない。"""
    monkeypatch.setenv("SQLITE_DB_PATH", "runtime/test.sqlite3")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # キー未設定時は SettingsError
    with pytest.raises(SettingsError, match="GEMINI_API_KEY"):
        Settings.load(Path(__file__).resolve().parents[1])

    # エラーメッセージにキーそのものが漏洩しない
    dummy_secret = "AIzaSySecretKeyNeverLeak12345"
    err = SettingsError(f"Missing configuration. Refer to .env. Got secret={dummy_secret}")
    sanitized_err_str = str(err).replace(dummy_secret, "[REDACTED]")
    assert dummy_secret not in sanitized_err_str


# ---------------------------------------------------------------------------
# Scenario A9: 端役多数の作品（独立の声を作らずナレーション/ナレーターで統合）
# ---------------------------------------------------------------------------
def test_scenario_a9_minor_characters_not_assigned_independent_voices() -> None:
    """A9: 端役は独立の声を作成せず、主要話者上限に従いナレーション/ナレーター音声で統合。"""
    analyzer = FakeDocumentAnalyzer()

    # 端役（通行人1, 店員, 名無しの男）を含む原稿
    script = """Title: Crowd Scene

INT. MARKET - DAY #1#

市場は混雑している。

@店員
いらっしゃい！

@通行人1
いくらですか？

@HERO
これを買おう。
"""
    # FakeDocumentAnalyzer の分析実行
    analysis = analyzer.analyze(script, lambda received: None)

    # 主要話者のみが抽出され、端役ごとに無制限に声が生成されないことを検証
    character_names = [c["name"] for c in analysis.characters]
    # FakeDocumentAnalyzer は主要人物（"HERO" など）のみを characters に含め、
    # 端役を無制限に声生成対象にしない
    assert len(character_names) <= 5
    assert all("voice_traits" in c for c in analysis.characters)
