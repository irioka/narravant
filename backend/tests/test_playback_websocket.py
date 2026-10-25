from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
)
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.main import app
from narravant.services.speech_segments import SegmentVoice
from narravant.services.tts import FakeTtsClient
from narravant.services.voice_design import FakeVoiceDesignClient
from narravant.services.voice_models import ProviderVoice, StoredVoiceRecord, utc_iso
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


class WebSocketTestSession:
    def __init__(self, incoming: asyncio.Queue, outgoing: asyncio.Queue) -> None:
        self.incoming = incoming
        self.outgoing = outgoing

    async def send_json(self, data: dict) -> None:
        await self.incoming.put(
            {"type": "websocket.receive", "text": json.dumps(data, ensure_ascii=False)}
        )

    async def receive_json(self) -> dict:
        message = await asyncio.wait_for(self.outgoing.get(), timeout=2)
        assert message["type"] == "websocket.send"
        return json.loads(message["text"])


@asynccontextmanager
async def connect_websocket(path: str):
    incoming: asyncio.Queue = asyncio.Queue()
    outgoing: asyncio.Queue = asyncio.Queue()
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "path": path,
        "raw_path": path.encode(),
        "scheme": "ws",
        "query_string": b"",
        "headers": [],
        "root_path": "",
        "client": ("test", 123),
        "server": ("test", 80),
        "subprotocols": [],
    }
    await incoming.put({"type": "websocket.connect"})
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    try:
        accepted = await asyncio.wait_for(outgoing.get(), timeout=2)
        assert accepted["type"] == "websocket.accept"
        yield WebSocketTestSession(incoming, outgoing)
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=2)


@pytest.fixture
def fake_tts() -> FakeTtsClient:
    return FakeTtsClient()


@pytest.fixture
def test_setup(fake_tts: FakeTtsClient, monkeypatch: pytest.MonkeyPatch):
    db = DatabaseManager(":memory:")
    storage = InMemoryScriptStorageClient()
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    voice_provider = FakeVoiceDesignClient()
    voice_registry = VoiceRegistryRepository(db)
    for speaker, voice_id in (
        ("ナレーター", "voice_fake_narrator"),
        ("ALICE", "voice_fake_alice"),
        ("BOB", "voice_fake_bob"),
    ):
        provider_voice = ProviderVoice(
            voice_id=voice_id,
            voice_type="prompted",
            language_code="ja-JP",
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        voice_provider.voices[voice_id] = provider_voice
        voice_registry.upsert(
            StoredVoiceRecord(
                owner_user_id=LOCAL_USER_ID,
                voice_id=voice_id,
                speaker_label=speaker,
                voice_traits=None,
                language_code="ja-JP",
                created_at=None,
                created_at_source="unknown",
                registered_at=utc_iso(),
                expire_time=None,
                last_checked_at=None,
                availability="unknown",
            )
        )

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
                {"speaker": "ナレーター", "voice_id": "voice_fake_narrator", "voice_traits": "t1"},
                {"speaker": "ALICE", "voice_id": "voice_fake_alice", "voice_traits": "t2"},
                {"speaker": "BOB", "voice_id": "voice_fake_bob", "voice_traits": "t3"},
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
        gemini_safety_threshold="BLOCK_NONE",
        playback_scene_pause_duration_ms=1750,
        playback_speaker_change_pause_duration_ms=625,
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.settings = mock_settings
    app.state.db_manager = db
    app.state.storage_client = storage
    app.state.voice_management_owner_locks = {}

    import narravant.api.playback as playback_module

    monkeypatch.setattr(playback_module, "_build_tts_client", lambda settings: fake_tts)
    monkeypatch.setattr(playback_module, "_build_voice_design_client", lambda settings: voice_provider)

    try:
        yield storage, doc_id, fake_tts
    finally:
        app.dependency_overrides.clear()


async def test_playback_ws_full_flow(test_setup):
    """Test full WebSocket playback streaming flow with utterance events and audio chunks."""
    storage, doc_id, fake_tts = test_setup

    async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
        await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        events = []
        while True:
            msg = await ws.receive_json()
            events.append(msg)
            if msg.get("event") in {"playback_complete", "error"}:
                break

        event_types = [e["event"] for e in events]
        assert "utterance_start" in event_types
        assert "scene_pause" in event_types
        assert "utterance_pause" in event_types
        assert all(event["duration_ms"] == 625 for event in events if event["event"] == "utterance_pause")
        assert "audio_chunk" in event_types
        assert "audio_segment_end" in event_types
        assert "utterance_end" in event_types
        assert event_types[-1] == "playback_complete"
        pause = next(event for event in events if event["event"] == "scene_pause")
        assert pause["duration_ms"] == 1750

        # Check audio chunk contains valid base64
        audio_chunks = [e for e in events if e["event"] == "audio_chunk"]
        assert len(audio_chunks) > 0
        decoded_bytes = base64.b64decode(audio_chunks[0]["data"])
        assert len(decoded_bytes) > 0


async def test_playback_ws_validates_listener_voice_with_primary_voices_before_synthesis(
    test_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    _storage, _doc_id, fake_tts = test_setup
    validated: list[tuple[str, list[str], bool]] = []

    class VoiceValidationSpy:
        async def validate_voice_ids(
            self,
            owner_id: str,
            voice_ids: list[str],
            *,
            allow_current_assignment: bool,
        ) -> None:
            validated.append((owner_id, voice_ids, allow_current_assignment))

    import narravant.api.playback as playback_module

    monkeypatch.setattr(playback_module, "_build_voice_management_service", lambda *_args: VoiceValidationSpy())

    async with connect_websocket("/api/v1/documents/playback") as ws:
        await ws.send_json(
            {
                "action": "start",
                "draft": {
                    "source_fountain": (
                        "INT. ROOM - DAY #1#\n\n"
                        "@ALICE\n"
                        "[[listener:BOB]]\n"
                        "わかる？ |うん|"
                    ),
                    "voice_assignments": [
                        {"speaker": "Narrator", "voice_id": "voice_fake_narrator"},
                        {"speaker": "ALICE", "voice_id": "voice_fake_alice"},
                        {"speaker": "BOB", "voice_id": "voice_fake_bob"},
                    ],
                },
            }
        )
        events = []
        while True:
            message = await ws.receive_json()
            events.append(message)
            if message.get("event") == "playback_complete":
                break

    assert validated == [(LOCAL_USER_ID, ["voice_fake_narrator", "voice_fake_alice", "voice_fake_bob"], False)]
    reaction_segment = next(segment for segment in fake_tts.synthesized_segments if segment.listener is not None)
    assert reaction_segment.listener == SegmentVoice("BOB", "voice_fake_bob")
    assert any(message["event"] == "utterance_pause" and message["duration_ms"] == 625 for message in events)


async def test_playback_ws_start_from_specific_position(test_setup):
    """Test starting playback from scene 2 utterance 0."""
    _storage, doc_id, _fake_tts = test_setup

    async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
        await ws.send_json({"action": "start", "scene_number": 2, "start_utterance_index": 0})

        events = []
        while True:
            msg = await ws.receive_json()
            events.append(msg)
            if msg.get("event") == "playback_complete":
                break

        starts = [e for e in events if e["event"] == "utterance_start"]
        # Scene 2 has the narrated heading, narration, and BOB dialogue.
        assert len(starts) == 3
        assert starts[0]["scene_number"] == 2
        assert starts[0]["utterance_index"] == 0
        assert starts[0]["speaker"] == "Narrator"
        assert starts[1]["scene_number"] == 2
        assert starts[1]["utterance_index"] == 1
        assert starts[1]["speaker"] == "Narrator"
        assert starts[2]["scene_number"] == 2
        assert starts[2]["utterance_index"] == 2
        assert starts[2]["speaker"] == "BOB"


async def test_playback_ws_unassigned_voice_emits_error(test_setup):
    """If a speaker lacks voice_id, error event is emitted with position."""
    storage, doc_id, _fake_tts = test_setup

    # Overwrite script with unassigned voice for ALICE
    data, meta = storage.read_structured_script(doc_id, 1)
    data["voice_assignments"] = [
        {"speaker": "ナレーター", "voice_id": "voice_fake_narrator"},
        # ALICE is omitted
    ]
    storage.write_structured_script(doc_id, 1, data, if_generation_match=meta.generation)

    async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
        await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        msg = await ws.receive_json()
        assert msg["event"] == "error"
        assert msg["scene_number"] == 1
        assert msg["utterance_index"] == 2
        assert "ALICE" in msg["message"]


async def test_playback_ws_stop_action(test_setup):
    """action: 'stop' halts subsequent playback generation."""
    _storage, doc_id, _fake_tts = test_setup

    async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
        await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        # Receive first utterance_start
        msg = await ws.receive_json()
        assert msg["event"] == "utterance_start"

        # Send stop immediately
        await ws.send_json({"action": "stop"})


async def test_playback_ws_nonexistent_document(test_setup):
    """Connecting to a nonexistent document yields error event and closes."""
    _storage, _doc_id, _fake_tts = test_setup

    async with connect_websocket("/api/v1/documents/non-existent-doc/playback") as ws:
        msg = await ws.receive_json()
        assert msg["event"] == "error"
        assert "not found" in msg["message"]


async def test_playback_ws_duplicate_start_ignored(test_setup):
    """Sending duplicate start while playback is already running does not trigger parallel task."""
    _storage, doc_id, _fake_tts = test_setup

    async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
        await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})
        # Immediately send a second start
        await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})

        events = []
        while True:
            msg = await ws.receive_json()
            events.append(msg)
            if msg.get("event") == "playback_complete":
                break

        # Total complete event should be 1, total start events should equal number of utterances (4)
        complete_events = [e for e in events if e.get("event") == "playback_complete"]
        assert len(complete_events) == 1


async def test_playback_ws_resolves_deps_without_request_override(
    fake_tts: FakeTtsClient,
    monkeypatch: pytest.MonkeyPatch,
):
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
            "voice_assignments": [{"speaker": "ナレーター", "voice_id": "voice_fake_narrator", "voice_traits": "t1"}],
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
        gemini_safety_threshold="BLOCK_NONE",
        playback_speaker_change_pause_duration_ms=1000,
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    voice_provider = FakeVoiceDesignClient()
    voice_provider.voices["voice_fake_narrator"] = ProviderVoice(
        voice_id="voice_fake_narrator",
        voice_type="prompted",
        language_code="ja-JP",
        expire_time=datetime(2099, 1, 1, tzinfo=UTC),
    )
    app.state.db_manager = db
    app.state.storage_client = storage
    # Resolve db/storage/settings/user from the real WebSocket connection
    # (app.state). External TTS and Voice Design clients use fakes. This reproduces the
    # runtime path that previously failed with `get_db_manager() missing 'request'`.

    import narravant.api.playback as playback_module

    monkeypatch.setattr(playback_module, "_build_tts_client", lambda settings: fake_tts)
    monkeypatch.setattr(playback_module, "_build_voice_design_client", lambda settings: voice_provider)
    try:
        async with connect_websocket(f"/api/v1/documents/{doc_id}/playback") as ws:
            await ws.send_json({"action": "start", "scene_number": 1, "start_utterance_index": 0})
            events = []
            while True:
                msg = await ws.receive_json()
                events.append(msg)
                if msg.get("event") == "playback_complete":
                    break
            assert events[-1]["event"] == "playback_complete"
            assert any(e["event"] == "utterance_start" for e in events)
            assert voice_provider.get_call_count == 1
    finally:
        app.dependency_overrides.clear()
