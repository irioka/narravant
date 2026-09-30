from __future__ import annotations

import asyncio
import base64
import json

import pytest

from narravant.services.playback import PlannedUtterance
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

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _StubAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object) -> _StubResponse:
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
            voice_id="voices/fake-voice-1",
        )
    ]
    assert len(chunks) == 2
    assert all(isinstance(c, bytes) and len(c) > 0 for c in chunks)
    assert fake_tts.call_count == 1
    assert fake_tts.synthesized_utterances[0] == ("こんにちは。", "voices/fake-voice-1")


@pytest.mark.asyncio
async def test_fake_tts_failure_injection(fake_tts: FakeTtsClient):
    """Injected failure raises TtsError on targeted utterance."""
    fake_tts.fail_on_utterance_text = "失敗する台詞"

    with pytest.raises(TtsError, match="Simulated TTS failure"):
        async for _ in fake_tts.synthesize_stream(
            text="この台詞は失敗する台詞です。",
            voice_id="voices/fake-voice-1",
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
            voice_id="voices/fake-narrator",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=1,
            speaker="アリス",
            target_type="character",
            text="誰かいるの？",
            voice_id="voices/fake-alice",
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
        "utterance_end",
        "utterance_start",
        "audio_chunk",
        "audio_chunk",
        "utterance_end",
        "playback_complete",
    ]

    # Verify first utterance start metadata
    assert events[0]["scene_number"] == 1
    assert events[0]["utterance_index"] == 0
    assert events[0]["speaker"] == "ナレーター"

    # Verify second utterance start metadata
    assert events[4]["scene_number"] == 1
    assert events[4]["utterance_index"] == 1
    assert events[4]["speaker"] == "アリス"


@pytest.mark.asyncio
async def test_tts_coordinator_inserts_a_pause_between_scenes(fake_tts: FakeTtsClient):
    utterances = [
        PlannedUtterance(1, 0, "ナレーター", "narrator", "第一場面", "voice-1"),
        PlannedUtterance(2, 0, "ナレーター", "narrator", "第二場面", "voice-1"),
    ]

    events = [event async for event in TtsPlaybackCoordinator(fake_tts).run_stream(utterances)]
    pause_index = next(index for index, event in enumerate(events) if event["type"] == "scene_pause")

    assert events[pause_index] == {"type": "scene_pause", "scene_number": 2, "duration_ms": 3000}
    assert events[pause_index + 1]["type"] == "utterance_start"


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
            voice_id="voices/fake-narrator",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=1,
            speaker="アリス",
            target_type="character",
            text="ここで失敗する。",
            voice_id="voices/fake-alice",
        ),
        PlannedUtterance(
            scene_number=1,
            utterance_index=2,
            speaker="ボブ",
            target_type="character",
            text="実行されてはならない台詞。",
            voice_id="voices/fake-bob",
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
            voice_id=f"voices/fake-{i}",
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
