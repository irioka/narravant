from __future__ import annotations

import asyncio
import base64
import json

import pytest

from narravant.core.speech_notation import parse_speech_notation
from narravant.services.playback import PlannedUtterance, build_playback_plan
from narravant.services.speech_segments import SegmentVoice
from narravant.services.tts import (
    FakeTtsClient,
    GeminiTtsClient,
    TtsError,
    TtsPlaybackCoordinator,
)


class _StubResponse:
    def __init__(self, status_code: int, *, detail: bytes = b"", lines: list[str] | None = None) -> None:
        self.status_code = status_code
        self._detail = detail
        self._lines = lines or []

    async def __aenter__(self) -> _StubResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def aread(self) -> bytes:
        return self._detail

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StubAsyncClient:
    responses: list[_StubResponse] = []
    requests: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _StubAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object) -> _StubResponse:
        self.requests.append((_args, _kwargs))
        return self.responses.pop(0)


@pytest.fixture
def fake_tts() -> FakeTtsClient:
    return FakeTtsClient()


@pytest.mark.asyncio
async def test_fake_tts_synthesize_stream_returns_chunks(fake_tts: FakeTtsClient):
    """Fake TTS returns deterministic chunks without calling external APIs."""
    chunks = [
        chunk
        async for chunk in fake_tts.synthesize_stream(
            text="こんにちは。",
            voice_id="voice_fake_1",
        )
    ]
    assert len(chunks) == 2
    assert all(isinstance(c, bytes) and len(c) > 0 for c in chunks)
    assert fake_tts.call_count == 1
    assert fake_tts.synthesized_utterances[0] == ("こんにちは。", "voice_fake_1")


@pytest.mark.asyncio
async def test_fake_tts_failure_injection(fake_tts: FakeTtsClient):
    """Injected failure raises TtsError on targeted utterance."""
    fake_tts.fail_on_utterance_text = "失敗する台詞"

    with pytest.raises(TtsError, match="Simulated TTS failure"):
        async for _ in fake_tts.synthesize_stream(
            text="この台詞は失敗する台詞です。",
            voice_id="voice_fake_1",
        ):
            pass


@pytest.mark.asyncio
async def test_gemini_tts_retries_transient_503_before_audio(monkeypatch: pytest.MonkeyPatch):
    audio = base64.b64encode(b"\x00\x01").decode("ascii")
    payload = json.dumps({"candidates": [{"content": {"parts": [{"inlineData": {"data": audio}}]}}]})
    _StubAsyncClient.responses = [
        _StubResponse(503, detail=b'{"error":{"status":"UNAVAILABLE"}}'),
        _StubResponse(200, lines=[f"data: {payload}"]),
    ]
    monkeypatch.setattr("narravant.services.tts.httpx.AsyncClient", _StubAsyncClient)

    client = GeminiTtsClient(api_key="test", max_attempts=2, retry_backoff_seconds=0)
    chunks = [chunk async for chunk in client.synthesize_stream(text="テスト", voice_id="voice-1")]

    assert chunks == [b"\x00\x01"]
    assert _StubAsyncClient.responses == []


@pytest.mark.asyncio
async def test_gemini_tts_rejects_custom_two_speaker_segment_before_provider_request(
    monkeypatch: pytest.MonkeyPatch,
):
    _StubAsyncClient.responses = []
    _StubAsyncClient.requests = []
    monkeypatch.setattr("narravant.services.tts.httpx.AsyncClient", _StubAsyncClient)
    text = "この道で |うん| 間違いない？"
    utterance = PlannedUtterance(
        scene_number=1,
        utterance_index=0,
        speaker="旅人",
        target_type="character",
        text=text,
        voice_id="voice_test_primary",
        notation=parse_speech_notation(text, listener="案内人"),
        listener_voice=SegmentVoice("案内人", "voice_test_listener"),
    )
    client = GeminiTtsClient(api_key="", language_code="ja-JP", retry_backoff_seconds=0)

    events = [event async for event in TtsPlaybackCoordinator(client).run_stream([utterance])]

    assert [event["type"] for event in events] == ["utterance_start", "error"]
    assert events[-1]["scene_number"] == 1
    assert events[-1]["utterance_index"] == 0
    assert "Gemini TTS では" in events[-1]["message"]
    assert "二話者" in events[-1]["message"]
    assert _StubAsyncClient.requests == []


@pytest.mark.asyncio
async def test_gemini_tts_does_not_retry_non_transient_400(monkeypatch: pytest.MonkeyPatch):
    _StubAsyncClient.responses = [_StubResponse(400, detail=b'{"error":{"status":"INVALID_ARGUMENT"}}')]
    monkeypatch.setattr("narravant.services.tts.httpx.AsyncClient", _StubAsyncClient)

    client = GeminiTtsClient(api_key="test", max_attempts=3, retry_backoff_seconds=0)
    with pytest.raises(TtsError, match=r"Gemini TTS failed \(400\)"):
        async for _ in client.synthesize_stream(text="テスト", voice_id="voice-1"):
            pass

    assert _StubAsyncClient.responses == []


@pytest.mark.asyncio
async def test_tts_coordinator_iterates_utterances_in_order(fake_tts: FakeTtsClient):
    """Coordinator iterates planned utterances in sequence and yields events."""
    utterances = [
        PlannedUtterance(
            scene_number=1,
            utterance_index=0,
            speaker="ナレーター",
            target_type="narrator",
            text="静かな部屋。",
            voice_id="voice_fake_narrator",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=1,
            speaker="アリス",
            target_type="character",
            text="誰かいるの？",
            voice_id="voice_fake_alice",
        ),
    ]

    coordinator = TtsPlaybackCoordinator(tts_client=fake_tts)
    events = []

    async for event in coordinator.run_stream(utterances):
        events.append(event)

    event_types = [e["type"] for e in events]
    assert event_types == [
        "utterance_start",
        "audio_chunk",
        "audio_chunk",
        "audio_segment_end",
        "utterance_end",
        "utterance_start",
        "audio_chunk",
        "audio_chunk",
        "audio_segment_end",
        "utterance_end",
        "playback_complete",
    ]

    # Verify first utterance start metadata
    assert events[0]["scene_number"] == 1
    assert events[0]["utterance_index"] == 0
    assert events[0]["speaker"] == "ナレーター"

    # Verify second utterance start metadata
    assert events[5]["scene_number"] == 1
    assert events[5]["utterance_index"] == 1
    assert events[5]["speaker"] == "アリス"


@pytest.mark.asyncio
async def test_tts_coordinator_does_not_add_fixed_pause_within_a_scene(fake_tts: FakeTtsClient):
    utterances = [
        PlannedUtterance(1, 0, "ナレーター", "narrator", "ひとつ。", "voice-1"),
        PlannedUtterance(1, 1, "ナレーター", "narrator", "ふたつ。", "voice-1"),
    ]

    events = [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream(utterances)]

    assert "utterance_pause" not in [event["type"] for event in events]


@pytest.mark.asyncio
@pytest.mark.parametrize("duration_ms", [1000, 650, 0])
async def test_speaker_change_pause_preserves_sentence_and_scene_boundaries(fake_tts, duration_ms):
    utterances = [
        PlannedUtterance(1, 0, "ナレーター", "narrator", "最初の文。次の文。", "voice-narrator"),
        PlannedUtterance(1, 1, "Narrator", "narrator", "続き。", "voice-narrator"),
        PlannedUtterance(1, 2, "アリス", "character", "こんにちは。元気？", "voice-shared"),
        PlannedUtterance(1, 3, "アリス（小声）", "character", "もう一言。", "voice-shared"),
        PlannedUtterance(1, 4, "ボブ", "character", "元気だよ。", "voice-shared"),
        PlannedUtterance(1, 5, "ナレーター", "narrator", "二人は歩き出した。", "voice-narrator"),
        PlannedUtterance(2, 0, "アリス", "character", "次の場面。", "voice-shared"),
    ]
    coordinator = TtsPlaybackCoordinator(fake_tts, speaker_change_pause_duration_ms=duration_ms)
    events = [event async for event in coordinator.run_stream(utterances)]
    pauses = [event for event in events if event["type"] == "utterance_pause"]
    assert [event["duration_ms"] for event in pauses] == ([duration_ms] * 3 if duration_ms else [])
    for index, event in enumerate(events):
        if event["type"] == "utterance_pause":
            assert events[index + 1]["type"] == "audio_chunk"
            assert event["scene_number"] == 1
    assert len([event for event in events if event["type"] == "scene_pause"]) == 1
    assert events[-1]["type"] == "playback_complete"
    resumed = [event async for event in coordinator.run_stream(utterances[2:3])]
    assert not any(event["type"] in {"utterance_pause", "scene_pause"} for event in resumed)


@pytest.mark.asyncio
@pytest.mark.parametrize("speaker", ["Narrator", "MAYA"])
@pytest.mark.parametrize("duration_ms", [1000, 650, 0])
async def test_scene_heading_to_speech_uses_configured_pause_once(fake_tts, speaker, duration_ms):
    data = {
        "source_fountain": f"INT. ROOM - DAY #1#\n\n@{speaker}\nFirst sentence. Second sentence.\n",
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voice-narrator"},
            {"speaker": "MAYA", "voice_id": "voice-maya"},
        ],
    }
    plan = build_playback_plan(data)
    coordinator = TtsPlaybackCoordinator(fake_tts, speaker_change_pause_duration_ms=duration_ms)
    events = [event async for event in coordinator.run_stream(plan.utterances)]
    pauses = [event for event in events if event["type"] == "utterance_pause"]
    assert [event["duration_ms"] for event in pauses] == ([duration_ms] if duration_ms else [])
    assert events[-1]["type"] == "playback_complete"
    resumed = build_playback_plan(data, start_utterance_index=1)
    resumed_events = [event async for event in coordinator.run_stream(resumed.utterances)]
    assert not any(event["type"] == "utterance_pause" for event in resumed_events)


@pytest.mark.asyncio
async def test_tts_coordinator_synthesizes_each_sentence_as_its_own_segment(fake_tts: FakeTtsClient):
    utterance = PlannedUtterance(
        1, 0, "ナレーター", "narrator", "最初の文です。次の文です！", "voice-1", "穏やかに読む"
    )

    events = [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream([utterance])]

    assert [text for text, _voice in fake_tts.synthesized_utterances] == ["最初の文です。", "次の文です！"]
    assert [voice for _text, voice in fake_tts.synthesized_utterances] == ["voice-1", "voice-1"]
    assert fake_tts.synthesized_styles == ["穏やかに読む", "穏やかに読む"]
    assert [event["type"] for event in events].count("utterance_start") == 1
    assert [event["type"] for event in events].count("utterance_end") == 1
    assert [event["type"] for event in events].count("audio_segment_end") == 2
    segment_end = next(index for index, event in enumerate(events) if event["type"] == "audio_segment_end")
    assert events[segment_end + 1]["type"] == "audio_chunk"
    assert "utterance_pause" not in [event["type"] for event in events]


@pytest.mark.asyncio
async def test_tts_coordinator_splits_long_sentence_at_clause_punctuation(fake_tts: FakeTtsClient):
    text = f"{'あ' * 130}、{'い' * 130}。"
    utterance = PlannedUtterance(1, 0, "ナレーター", "narrator", text, "voice-1")

    [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream([utterance])]

    pieces = [text for text, _voice in fake_tts.synthesized_utterances]
    assert pieces == [f"{'あ' * 130}、", f"{'い' * 130}。"]
    assert all(len(piece) <= 180 for piece in pieces)


@pytest.mark.asyncio
async def test_tts_coordinator_inserts_a_pause_between_scenes(fake_tts: FakeTtsClient):
    utterances = [
        PlannedUtterance(1, 0, "ナレーター", "narrator", "第一場面", "voice-1"),
        PlannedUtterance(2, 0, "ナレーター", "narrator", "第二場面", "voice-1"),
    ]

    events = [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream(utterances)]
    pause_index = next(index for index, event in enumerate(events) if event["type"] == "scene_pause")

    assert events[pause_index] == {"type": "scene_pause", "scene_number": 2, "duration_ms": 3000}
    assert events[pause_index - 1]["type"] == "utterance_end"
    assert events[pause_index - 1]["scene_number"] == 1
    assert events[pause_index + 1]["type"] == "utterance_start"
    assert events[pause_index + 1]["scene_number"] == 2


@pytest.mark.asyncio
async def test_tts_coordinator_only_pauses_when_the_scene_changes(
    fake_tts: FakeTtsClient,
):
    utterances = [
        PlannedUtterance(1, 0, "アリス", "character", "聞こえる？", "voice-alice"),
        PlannedUtterance(1, 1, "ボブ", "character", "ああ。", "voice-bob"),
        PlannedUtterance(2, 0, "ナレーター", "narrator", "次の部屋。", "voice-narrator"),
    ]

    events = [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream(utterances)]

    pauses = [event for event in events if event["type"] in {"utterance_pause", "scene_pause"}]
    assert pauses == [{"type": "scene_pause", "scene_number": 2, "duration_ms": 3000}]


@pytest.mark.asyncio
async def test_tts_coordinator_stops_immediately_on_failure(fake_tts: FakeTtsClient):
    """When an utterance fails, coordinator yields error event and halts subsequent generation."""
    fake_tts.fail_on_utterance_text = "失敗"

    utterances = [
        PlannedUtterance(
            scene_number=1,
            utterance_index=0,
            speaker="ナレーター",
            target_type="narrator",
            text="最初の台詞。",
            voice_id="voice_fake_narrator",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=1,
            speaker="アリス",
            target_type="character",
            text="ここで失敗する。",
            voice_id="voice_fake_alice",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=2,
            speaker="ボブ",
            target_type="character",
            text="実行されてはならない台詞。",
            voice_id="voice_fake_bob",
        ),
    ]

    coordinator = TtsPlaybackCoordinator(tts_client=fake_tts)
    events = []

    async for event in coordinator.run_stream(utterances):
        events.append(event)

    event_types = [e["type"] for e in events]
    assert "error" in event_types
    # Third utterance should never be called
    assert fake_tts.call_count == 2
    assert "ボブ" not in [u[1] for u in fake_tts.synthesized_utterances]

    error_event = next(e for e in events if e["type"] == "error")
    assert error_event["scene_number"] == 1
    assert error_event["utterance_index"] == 1
    assert "Simulated TTS failure" in error_event["message"]


@pytest.mark.asyncio
async def test_tts_coordinator_cancellation_cancels_active_task(fake_tts: FakeTtsClient):
    """Cancelling the running task immediately halts synthesis (SC-1: cost control on stop/disconnect)."""
    utterances = [
        PlannedUtterance(
            scene_number=1,
            utterance_index=i,
            speaker=f"Speaker{i}",
            target_type="character",
            text=f"台詞番号 {i}",
            voice_id=f"voice_fake_{i}",
        )
        for i in range(10)
    ]

    coordinator = TtsPlaybackCoordinator(tts_client=fake_tts)
    received_events = []

    async def consumer():
        async for event in coordinator.run_stream(utterances):
            received_events.append(event)
            if event["type"] == "utterance_end" and event["utterance_index"] == 1:
                # Signal stop
                coordinator.stop()

    task = asyncio.create_task(consumer())
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Synthesis should have stopped after early cancellation, well before all 10 utterances
    assert fake_tts.call_count <= 3
    event_types = [e["type"] for e in received_events]
    assert "playback_complete" not in event_types
