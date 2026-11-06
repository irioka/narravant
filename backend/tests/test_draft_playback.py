"""未保存 Import の再生を、実 API と永続ストレージなしで検証する。"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from narravant.api import playback
from narravant.api.dependencies import LOCAL_USER_DISPLAY_NAME, LOCAL_USER_EMAIL, LOCAL_USER_ID
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.services.speech_segments import SegmentVoice
from narravant.services.tts import FakeTtsClient
from narravant.services.voice_design import FakeVoiceDesignClient
from narravant.services.voice_models import ProviderVoice, StoredVoiceRecord, parse_provider_datetime, utc_iso

DRAFT = {
    "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\nFirst.\n\n@ALICE\nSecond.\n",
    "voice_assignments": [
        {"speaker": "Narrator", "voice_id": "voice_synthetic_narrator"},
        {"speaker": "ALICE", "voice_id": "voice_synthetic_alice"},
    ],
}


@pytest.fixture
def draft_connection(monkeypatch):
    tts = FakeTtsClient()
    provider = FakeVoiceDesignClient()
    db = DatabaseManager(":memory:")
    db.init_schema()
    DocumentRepository(db).ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    registry = VoiceRegistryRepository(db)
    for speaker, voice_id in (
        ("Narrator", "voice_synthetic_narrator"),
        ("ALICE", "voice_synthetic_alice"),
        ("Guide", "voice_synthetic_guide"),
    ):
        provider.voices[voice_id] = ProviderVoice(
            voice_id=voice_id,
            voice_type="prompted",
            language_code="ja-JP",
            expire_time=parse_provider_datetime("2099-01-01T00:00:00Z"),
        )
        registry.upsert(
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
    monkeypatch.setattr(playback, "_build_tts_client", lambda settings: tts)
    monkeypatch.setattr(playback, "_build_voice_design_client", lambda settings: provider)
    app = FastAPI()
    app.include_router(playback.router)
    # Draft playback may read the owner-scoped voice registry, but never a saved
    # document or script object.
    app.state.settings = SimpleNamespace(
        playback_scene_pause_duration_ms=0, playback_speaker_change_pause_duration_ms=1000,
        playback_narrator_sentence_pause_duration_ms=0,
    )
    app.state.db_manager = db
    app.state.storage_client = SimpleNamespace()

    @asynccontextmanager
    async def connect(path="/api/v1/documents/playback"):
        if path != "/api/v1/documents/playback":
            app.state.db_manager = db
            app.state.storage_client = SimpleNamespace(read_structured_script=lambda *_args: (DRAFT, None))
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        scope = {
            "type": "websocket",
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
            yield incoming, outgoing, tts
        finally:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, timeout=2)

    return connect


async def send_start(incoming, draft, start_utterance_index=1, **options):
    await incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "action": "start",
                    "scene_number": 1,
                    "start_utterance_index": start_utterance_index,
                    "draft": draft,
                    **options,
                }
            ),
        }
    )


async def receive_event(outgoing):
    response = await asyncio.wait_for(outgoing.get(), timeout=2)
    assert response["type"] == "websocket.send"
    return json.loads(response["text"])


@pytest.mark.parametrize("style", ["穏やかに", "Calm and unhurried", "😀" * 500])
async def test_draft_narrator_style_reaches_tts_and_not_character(draft_connection, style):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, DRAFT, 0, narrator_style=style)
        while True:
            event = await receive_event(outgoing)
            if event["event"] in {"playback_complete", "error"}:
                assert event["event"] == "playback_complete"
                break
        assert tts.synthesized_styles[:2] == [style, style]
        assert tts.synthesized_styles[-1] == ""
        assert "narrator_style" not in DRAFT


@pytest.mark.parametrize("style", [123, {}, "😀" * 501])
async def test_draft_invalid_narrator_style_is_rejected_before_tts(draft_connection, style):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, DRAFT, 0, narrator_style=style)
        event = await receive_event(outgoing)
        assert event["code"] == "INVALID_PLAYBACK_REQUEST"
        assert tts.call_count == 0
        assert "😀" not in json.dumps(event)


async def test_unsaved_draft_plays_scene_heading_without_persisting(draft_connection):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, DRAFT, start_utterance_index=0)
        events = []
        while True:
            event = await receive_event(outgoing)
            events.append(event)
            if event["event"] in ("error", "playback_complete"):
                break
        assert events[-1]["event"] == "playback_complete"
        starts = [event for event in events if event["event"] == "utterance_start"]
        assert [(event["speaker"], event["utterance_index"]) for event in starts] == [
            ("Narrator", 0),
            ("Narrator", 1),
            ("ALICE", 2),
        ]
        assert [event["duration_ms"] for event in events if event["event"] == "utterance_pause"] == [1000]
        assert [event["duration_ms"] for event in events if event["event"] == "scene_pause"] == [0]
        assert tts.calls == [
            {"text": "ROOM - DAY", "voice_id": "voice_synthetic_narrator"},
            {"text": "First.", "voice_id": "voice_synthetic_narrator"},
            {"text": "Second.", "voice_id": "voice_synthetic_alice"},
        ]


async def test_unsaved_draft_resume_starts_from_selected_position_without_persistence(draft_connection):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, DRAFT, start_utterance_index=2)
        events = []
        while True:
            event = await receive_event(outgoing)
            events.append(event)
            if event["event"] in ("error", "playback_complete"):
                break
        assert events[-1]["event"] == "playback_complete"
        starts = [event for event in events if event["event"] == "utterance_start"]
        assert [(event["speaker"], event["utterance_index"]) for event in starts] == [("ALICE", 2)]
        assert tts.calls == [{"text": "Second.", "voice_id": "voice_synthetic_alice"}]


async def test_draft_missing_voice_fails_before_synthesis(draft_connection):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, {**DRAFT, "voice_assignments": DRAFT["voice_assignments"][:1]})
        event = await receive_event(outgoing)
        assert event["event"] == "error"
        assert "ALICE" in event["message"]
        assert event["utterance_index"] == 2
        assert tts.calls == []


async def test_draft_reaction_uses_listener_voice_once_without_speaking_note(draft_connection):
    draft = {
        "source_fountain": ("INT. ROOM - DAY #1#\n\n@Narrator\n[[listener:Guide]]\nこの先で待って。 |わかった|\n"),
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voice_synthetic_narrator"},
            {"speaker": "Guide", "voice_id": "voice_synthetic_guide"},
        ],
    }
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, draft)
        events = []
        while True:
            event = await receive_event(outgoing)
            events.append(event)
            if event["event"] in ("error", "playback_complete"):
                break

        assert events[-1]["event"] == "playback_complete"
        assert tts.calls == [{"text": "この先で待って。 |わかった|", "voice_id": "voice_synthetic_narrator"}]
        assert len(tts.synthesized_segments) == 1
        assert tts.synthesized_segments[0].listener == SegmentVoice("Guide", "voice_synthetic_guide")
        assert "[[listener:Guide]]" not in tts.synthesized_segments[0].text


async def test_draft_listener_without_voice_fails_before_synthesis(draft_connection):
    draft = {
        "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\n[[listener:Guide]]\nWait. |Okay|",
        "voice_assignments": [{"speaker": "Narrator", "voice_id": "voice_synthetic_narrator"}],
    }
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, draft)
        event = await receive_event(outgoing)

        assert event["event"] == "error"
        assert "Guide" in event["message"]
        assert tts.calls == []


async def test_draft_expired_listener_fails_before_synthesis(draft_connection):
    draft = {
        "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\n[[listener:Guide]]\nWait? |Okay|",
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voice_synthetic_narrator"},
            {"speaker": "Guide", "voice_id": "voice_synthetic_guide"},
        ],
    }
    provider = playback._build_voice_design_client(None)
    provider.voices["voice_synthetic_guide"] = ProviderVoice(
        voice_id="voice_synthetic_guide",
        voice_type="prompted",
        language_code="ja-JP",
        expire_time=parse_provider_datetime("2000-01-01T00:00:00Z"),
    )

    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, draft)
        event = await receive_event(outgoing)

        assert event["event"] == "error"
        assert event["code"] == "VOICE_EXPIRED"
        assert tts.calls == []


async def test_draft_deleted_custom_voice_fails_before_synthesis(draft_connection):
    async with draft_connection() as (incoming, outgoing, tts):
        # Provider state no longer contains Alice's registered voice.
        app_provider = playback._build_voice_design_client(None)
        app_provider.voices.pop("voice_synthetic_alice")
        await send_start(incoming, DRAFT, start_utterance_index=2)
        event = await receive_event(outgoing)
        assert event["event"] == "error"
        assert event["code"] == "VOICE_NOT_FOUND"
        assert tts.calls == []


async def test_draft_expired_custom_voice_fails_before_synthesis(draft_connection):
    async with draft_connection() as (incoming, outgoing, tts):
        provider = playback._build_voice_design_client(None)
        provider.voices["voice_synthetic_alice"] = ProviderVoice(
            voice_id="voice_synthetic_alice",
            voice_type="prompted",
            language_code="ja-JP",
            expire_time=parse_provider_datetime("2000-01-01T00:00:00Z"),
        )
        await send_start(incoming, DRAFT, start_utterance_index=2)
        event = await receive_event(outgoing)
        assert event["event"] == "error"
        assert event["code"] == "VOICE_EXPIRED"
        assert tts.calls == []


@pytest.mark.parametrize(
    "draft",
    [
        None,
        {"source_fountain": 123, "voice_assignments": []},
        {**DRAFT, "voice_assignments": [{"speaker": [], "voice_id": "fake"}]},
    ],
)
async def test_invalid_draft_is_rejected_before_synthesis(draft_connection, draft):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, draft)
        event = await receive_event(outgoing)
        assert event["event"] == "error"
        assert event["code"] == "INVALID_PLAYBACK_REQUEST"
        assert tts.calls == []


@pytest.mark.parametrize("action", ["stop", "disconnect"])
async def test_draft_stop_and_disconnect_cancel_synthesis(draft_connection, monkeypatch, action):
    async with draft_connection() as (incoming, outgoing, tts):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_stream(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
                yield b"synthetic"
            finally:
                cancelled.set()

        monkeypatch.setattr(tts, "synthesize_segment_stream", slow_stream)
        await send_start(incoming, DRAFT)
        await asyncio.wait_for(started.wait(), timeout=2)
        if action == "stop":
            await incoming.put({"type": "websocket.receive", "text": '{"action": "stop"}'})
        else:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(cancelled.wait(), timeout=2)


async def test_saved_document_still_plays_without_a_draft(draft_connection, monkeypatch):
    repo = SimpleNamespace(
        ensure_user=lambda *args: None,
        get_document=lambda _id: {"current_version_id": 1},
        list_documents=lambda **_kwargs: ([], 0),
    )
    monkeypatch.setattr(playback, "DocumentRepository", lambda db: repo)
    # 同じ ASGI fixture の app を保存済み経路にも使う。
    async with draft_connection("/api/v1/documents/saved/playback") as (incoming, outgoing, tts):
        await send_start(incoming, None)
        events = []
        while True:
            event = await receive_event(outgoing)
            events.append(event)
            if event["event"] in ("error", "playback_complete"):
                break
        assert events[-1]["event"] == "playback_complete"
        assert tts.calls == [
            {"text": "First.", "voice_id": "voice_synthetic_narrator"},
            {"text": "Second.", "voice_id": "voice_synthetic_alice"},
        ]


async def test_draft_playback_uses_assigned_default_delivery(draft_connection):
    import copy

    draft = copy.deepcopy(DRAFT)
    draft["voice_assignments"][1]["voice_traits"] = (
        "Voice: A synthetic actor voice.\nDelivery: Portray a fictional child naturally."
    )
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, draft)
        while True:
            event = await receive_event(outgoing)
            if event["event"] in ("error", "playback_complete"):
                break
        assert event["event"] == "playback_complete"
        assert tts.synthesized_styles == ["", "Portray a fictional child naturally."]
        assert [text for text, _ in tts.synthesized_utterances] == ["First.", "Second."]


@pytest.mark.parametrize("action", ["stop", "disconnect"])
async def test_flow_control_stops_generation_while_window_is_full(draft_connection, monkeypatch, action):
    async with draft_connection() as (incoming, outgoing, tts):
        produced = []
        closed = asyncio.Event()

        async def fast_stream(*args, **kwargs):
            try:
                for index in range(100):
                    produced.append(index)
                    yield b"\x01\x00" * 24000
            finally:
                closed.set()

        monkeypatch.setattr(tts, "synthesize_segment_stream", fast_stream)
        await send_start(incoming, DRAFT)
        buffers = []
        while len(buffers) < 32:
            event = await receive_event(outgoing)
            assert event["event"] != "error"
            if event["event"] == "audio_chunk":
                buffers.append(event)
        assert len(produced) == 32
        assert len({event["buffer_id"] for event in buffers}) == 32
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(outgoing.get(), timeout=0.03)
        if action == "stop":
            await incoming.put({"type": "websocket.receive", "text": '{"action":"stop"}'})
        else:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(closed.wait(), timeout=2)
        assert len(produced) == 32


async def test_flow_control_ack_releases_only_the_matching_buffer(draft_connection, monkeypatch):
    async with draft_connection() as (incoming, outgoing, tts):
        produced = []

        async def fast_stream(*args, **kwargs):
            for index in range(100):
                produced.append(index)
                yield b"\x01\x00" * 24000

        monkeypatch.setattr(tts, "synthesize_segment_stream", fast_stream)
        await send_start(incoming, DRAFT)
        ids = []
        while len(ids) < 32:
            event = await receive_event(outgoing)
            assert event["event"] != "error"
            if event["event"] == "audio_chunk":
                ids.append(event["buffer_id"])
        for buffer_id in [9999, True, str(ids[0]), ids[0], ids[0]]:
            await incoming.put({
                "type": "websocket.receive",
                "text": json.dumps({"action": "audio_consumed", "buffer_id": buffer_id}),
            })
        event = await receive_event(outgoing)
        assert event["event"] == "audio_chunk"
        assert event["buffer_id"] > max(ids)
        assert len(produced) == 33
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(outgoing.get(), timeout=0.03)


async def test_playback_flow_preserves_pcm_and_pause_duration(draft_connection, monkeypatch):
    payload = b"\x01\x00" * 53000
    draft = {**DRAFT, "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\nFirst.\n\n@Narrator\nSecond.\n"}
    async with draft_connection() as (incoming, outgoing, tts):
        calls = []

        async def large_stream(*args, **kwargs):
            calls.append(1)
            yield payload[:1]
            yield payload[1:]

        monkeypatch.setattr(tts, "synthesize_segment_stream", large_stream)
        await send_start(
            incoming, draft, narrator_sentence_pause_duration_ms=4500,
        )
        audio = []
        pauses = []
        ids = []
        while True:
            event = await receive_event(outgoing)
            assert event["event"] != "error"
            if event["event"] == "audio_chunk":
                chunk = base64.b64decode(event["data"])
                audio.append(chunk)
                assert len(chunk) <= 48000
                assert len(chunk) % 2 == 0
            if event["event"] == "utterance_pause":
                pauses.append(event["duration_ms"])
            if "buffer_id" in event:
                ids.append(event["buffer_id"])
                await incoming.put({
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "audio_consumed", "buffer_id": event["buffer_id"]}),
                })
            if event["event"] == "playback_complete":
                break
        assert len(calls) == 2
        assert b"".join(audio) == payload * 2
        assert sum(pauses) == 4500
        assert pauses == [1000, 1000, 1000, 1000, 500]
        assert ids == list(range(1, len(ids) + 1))


async def test_stop_awaits_provider_cleanup_and_old_acks_cannot_release_next_run(draft_connection, monkeypatch):
    async with draft_connection() as (incoming, outgoing, tts):
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleanup_finished = asyncio.Event()
        calls = []

        async def stream(*args, **kwargs):
            calls.append(1)
            try:
                for _ in range(100):
                    yield b"\x01\x00" * 24000
            finally:
                if len(calls) == 1:
                    cleanup_started.set()
                    await release_cleanup.wait()
                    cleanup_finished.set()

        monkeypatch.setattr(tts, "synthesize_segment_stream", stream)
        await send_start(incoming, DRAFT)
        old_ids = []
        while len(old_ids) < 32:
            event = await receive_event(outgoing)
            if event["event"] == "audio_chunk":
                old_ids.append(event["buffer_id"])
        await incoming.put({"type": "websocket.receive", "text": '{"action":"stop"}'})
        try:
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            await send_start(incoming, DRAFT)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(outgoing.get(), timeout=0.03)
            assert len(calls) == 1
        finally:
            release_cleanup.set()
        new_ids = []
        while len(new_ids) < 32:
            event = await receive_event(outgoing)
            if event["event"] == "audio_chunk":
                new_ids.append(event["buffer_id"])
        assert cleanup_finished.is_set()
        assert len(calls) == 2
        assert min(new_ids) > max(old_ids)
        for buffer_id in old_ids:
            await incoming.put({
                "type": "websocket.receive",
                "text": json.dumps({"action": "audio_consumed", "buffer_id": buffer_id}),
            })
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(outgoing.get(), timeout=0.03)
        await incoming.put({
            "type": "websocket.receive",
            "text": json.dumps({"action": "audio_consumed", "buffer_id": new_ids[0]}),
        })
        event = await receive_event(outgoing)
        assert event["buffer_id"] > max(new_ids)


@pytest.mark.parametrize("invalid_flow", [True, 1, "unknown"])
async def test_invalid_flow_control_is_rejected_before_tts(draft_connection, invalid_flow):
    async with draft_connection() as (incoming, outgoing, tts):
        await send_start(incoming, DRAFT, flow_control=invalid_flow)
        event = await receive_event(outgoing)
        assert event["code"] == "INVALID_PLAYBACK_REQUEST"
        assert tts.call_count == 0


async def test_duration_flow_starts_slow_next_tts_while_audio_is_still_buffered(draft_connection, monkeypatch):
    draft = {**DRAFT, "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\nFirst.\n\n@Narrator\nSecond.\n"}
    async with draft_connection() as (incoming, outgoing, tts):
        next_tts_started = asyncio.Event()
        release_next_tts = asyncio.Event()
        calls = []

        async def stream(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                # 20msずつ届く32秒の音声。32packetでは0.64秒にしかならない。
                for _ in range(1600):
                    yield b"\x01\x00" * 480
            else:
                next_tts_started.set()
                await release_next_tts.wait()
                yield b"\x02\x00" * 24000

        monkeypatch.setattr(tts, "synthesize_segment_stream", stream)
        await send_start(incoming, draft, narrator_sentence_pause_duration_ms=0)
        ids = []
        try:
            while len(ids) < 1600:
                event = await receive_event(outgoing)
                assert event["event"] != "error"
                if event["event"] == "audio_chunk":
                    ids.append(event["buffer_id"])
            assert not next_tts_started.is_set()
            await incoming.put({
                "type": "websocket.receive",
                "text": json.dumps({"action": "audio_consumed", "buffer_id": ids[0]}),
            })
            await asyncio.wait_for(next_tts_started.wait(), timeout=2)
            assert len(calls) == 2
            # 20秒の生成待ちを合成的に進めても、11.98秒の未再生音声が残る。
            for buffer_id in ids[1:1001]:
                await incoming.put({
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "audio_consumed", "buffer_id": buffer_id}),
                })
            release_next_tts.set()
            while True:
                event = await receive_event(outgoing)
                assert event["event"] != "error"
                if event["event"] == "audio_chunk":
                    assert base64.b64decode(event["data"]) == b"\x02\x00" * 24000
                    break
            assert len(ids) - 1001 == 599
        finally:
            release_next_tts.set()
