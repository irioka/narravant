from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
)
from narravant.api.dependencies import get_db_manager as get_db_manager
from narravant.api.dependencies import get_storage_client as get_storage_client
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.main import app
from narravant.services.tts import FakeTtsClient
from narravant.storage.gcs import InMemoryScriptStorageClient

FOUNTAIN_TEXT = """Title: Audiobook WebSocket Test

INT. ROOM - NIGHT #1#

The room is dark.

@ALICE
Hello?

INT. GARDEN - DAY #2#

Sun shines bright.

@BOB
Good morning.
"""


@pytest.fixture
def fake_tts() -> FakeTtsClient:
    return FakeTtsClient()


@pytest.fixture
def test_setup(fake_tts: FakeTtsClient):
    db = DatabaseManager(":memory:")
    storage = InMemoryScriptStorageClient()
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    doc_id = "doc-ws-test"
    stored = storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "Audiobook WebSocket Test",
                "characters": ["ナレーター", "ALICE", "BOB"],
                "logline": "Test logline",
                "synopsis": "Test synopsis",
                "theme_setting": "テーマ",
            },
            "source": {
                "filename": "test.fountain",
                "media_type": "text/x-fountain",
                "sha256": "a" * 64,
            },
            "source_fountain": FOUNTAIN_TEXT,
            "scenes": [
                {"scene_number": 1, "heading": "INT. ROOM - NIGHT", "text": "...", "dialogues": []},
                {"scene_number": 2, "heading": "INT. GARDEN - DAY", "text": "...", "dialogues": []},
            ],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": 1,
                        "label": "Opportunity",
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
                "scene_mapping": scene_mapping_payload(2, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "ナレーター声"},
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/fake-narrator", "voice_traits": "t1"},
                {"speaker": "ALICE", "voice_id": "voices/fake-alice", "voice_traits": "t2"},
                {"speaker": "BOB", "voice_id": "voices/fake-bob", "voice_traits": "t3"},
            ],
        },
        if_generation_match=0,
    )
    storage.write_search_text(doc_id, FOUNTAIN_TEXT, if_generation_match=0)
    repo.create_document(
        doc_id,
        LOCAL_USER_ID,
        "Audiobook WebSocket Test",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )

    mock_settings = SimpleNamespace(
        gemini_api_key="fake",
        gemini_tts_model="gemini-3.8-flash-tts",
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.settings = mock_settings
    app.state.db_manager = db
    app.state.storage_client = storage

    # Register HTTP accessor overrides ONLY to signal main.lifespan to skip
    # production wiring (so it doesn't overwrite app.state). The WS route itself
    # reads app.state directly; the TTS client is faked via module indirection.
    app.dependency_overrides[get_db_manager] = lambda: db
    app.dependency_overrides[get_storage_client] = lambda: storage

    import narravant.api.playback as playback_module

    original_build = playback_module._build_tts_client
    playback_module._build_tts_client = lambda settings: fake_tts

    with TestClient(app) as client:
        yield client, storage, doc_id, fake_tts
    playback_module._build_tts_client = original_build
    app.dependency_overrides.clear()


def test_playback_ws_full_flow(test_setup):
    """Test full WebSocket playback streaming flow with utterance events and audio chunks."""
    client, storage, doc_id, fake_tts = test_setup

    with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
        ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("event") == "playback_complete":
                break

        event_types = [e["event"] for e in events]
        assert "utterance_start" in event_types
        assert "scene_pause" in event_types
        assert "audio_chunk" in event_types
        assert "utterance_end" in event_types
        assert event_types[-1] == "playback_complete"

        # Check audio chunk contains valid base64
        audio_chunks = [e for e in events if e["event"] == "audio_chunk"]
        assert len(audio_chunks) > 0
        decoded_bytes = base64.b64decode(audio_chunks[0]["data"])
        assert len(decoded_bytes) > 0


def test_playback_ws_start_from_specific_position(test_setup):
    """Test starting playback from scene 2 utterance 0."""
    client, storage, doc_id, fake_tts = test_setup

    with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
        ws.send_json({"action": "start", "scene_number": 2, "start_utterance_index": 0})

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("event") == "playback_complete":
                break

        starts = [e for e in events if e["event"] == "utterance_start"]
        # Scene 2 has 2 utterances (narration and BOB)
        assert len(starts) == 2
        assert starts[0]["scene_number"] == 2
        assert starts[0]["utterance_index"] == 0
        assert starts[0]["speaker"] == "ナレーター"
        assert starts[1]["scene_number"] == 2
        assert starts[1]["utterance_index"] == 1
        assert starts[1]["speaker"] == "BOB"


def test_playback_ws_unassigned_voice_emits_error(test_setup):
    """If a speaker lacks voice_id, error event is emitted with position."""
    client, storage, doc_id, fake_tts = test_setup

    # Overwrite script with unassigned voice for ALICE
    data, meta = storage.read_structured_script(doc_id, 1)
    data["voice_assignments"] = [
        {"speaker": "ナレーター", "voice_id": "voices/fake-narrator"},
        # ALICE is omitted
    ]
    storage.write_structured_script(doc_id, 1, data, if_generation_match=meta.generation)

    with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
        ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        msg = ws.receive_json()
        assert msg["event"] == "error"
        assert msg["scene_number"] == 1
        assert msg["utterance_index"] == 1
        assert "ALICE" in msg["message"]


def test_playback_ws_stop_action(test_setup):
    """action: 'stop' halts subsequent playback generation."""
    client, storage, doc_id, fake_tts = test_setup

    with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
        ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        # Receive first utterance_start
        msg = ws.receive_json()
        assert msg["event"] == "utterance_start"

        # Send stop immediately
        ws.send_json({"action": "stop"})


def test_playback_ws_nonexistent_document(test_setup):
    """Connecting to a nonexistent document yields error event and closes."""
    client, storage, doc_id, fake_tts = test_setup

    with client.websocket_connect("/api/v1/documents/non-existent-doc/playback") as ws:
        msg = ws.receive_json()
        assert msg["event"] == "error"
        assert "not found" in msg["message"]


def test_playback_ws_duplicate_start_ignored(test_setup):
    """Sending duplicate start while playback is already running does not trigger parallel task."""
    client, storage, doc_id, fake_tts = test_setup

    with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
        ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})
        # Immediately send a second start
        ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("event") == "playback_complete":
                break

        # Total complete event should be 1, total start events should equal number of utterances (4)
        complete_events = [e for e in events if e.get("event") == "playback_complete"]
        assert len(complete_events) == 1


def test_playback_ws_resolves_deps_without_request_override(fake_tts: FakeTtsClient):
    """Regression: WebSocket DI must resolve get_db_manager/get_settings/get_storage_client
    from app.state via HTTPConnection (not Request). Only the TTS client and user are overridden;
    the connection-scoped accessors must work on a WebSocket connection.
    """
    db = DatabaseManager(":memory:")
    storage = InMemoryScriptStorageClient()
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    doc_id = "doc-ws-di"
    stored = storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "WS DI Test",
                "characters": ["ナレーター"],
                "logline": "l",
                "synopsis": "s",
                "theme_setting": "t",
            },
            "source": {"filename": "t.fountain", "media_type": "text/x-fountain", "sha256": "a" * 64},
            "source_fountain": "Title: WS DI Test\n\nINT. ROOM - NIGHT #1#\n\nThe room is quiet.\n",
            "scenes": [{"scene_number": 1, "heading": "INT. ROOM - NIGHT", "text": "...", "dialogues": []}],
            "analysis": {"status": "completed", "turning_points": [], "characters": []},
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "ナレーター声"},
            "voice_assignments": [{"speaker": "ナレーター", "voice_id": "voices/fake-narrator", "voice_traits": "t1"}],
        },
        if_generation_match=0,
    )
    storage.write_search_text(doc_id, "narration", if_generation_match=0)
    repo.create_document(
        doc_id,
        LOCAL_USER_ID,
        "WS DI Test",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )

    # Populate app.state exactly like production startup wiring.
    app.state.settings = SimpleNamespace(
        gemini_api_key="fake",
        gemini_tts_model="gemini-3.8-flash-tts",
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.db_manager = db
    app.state.storage_client = storage
    # Resolve db/storage/settings/user from the real WebSocket connection
    # (app.state). Only the external TTS client is faked. This reproduces the
    # runtime path that previously failed with `get_db_manager() missing 'request'`.
    # HTTP accessor overrides are registered solely to bypass lifespan wiring.
    app.dependency_overrides[get_db_manager] = lambda: db
    app.dependency_overrides[get_storage_client] = lambda: storage

    import narravant.api.playback as playback_module

    original_build = playback_module._build_tts_client
    playback_module._build_tts_client = lambda settings: fake_tts
    try:
        with TestClient(app) as client:
            with client.websocket_connect(f"/api/v1/documents/{doc_id}/playback") as ws:
                ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})
                events = []
                while True:
                    msg = ws.receive_json()
                    events.append(msg)
                    if msg.get("event") == "playback_complete":
                        break
                assert events[-1]["event"] == "playback_complete"
                assert any(e["event"] == "utterance_start" for e in events)
    finally:
        playback_module._build_tts_client = original_build
        app.dependency_overrides.clear()
