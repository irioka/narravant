"""再生秒数と件数の両方で先読みを制限し、消費通知で正確に補充する合成検査。"""

from __future__ import annotations

import asyncio

import pytest

from narravant.services.playback_flow import PlaybackBufferWindow


@pytest.mark.parametrize("frames", [1, 240, 480, 24000])
async def test_duration_window_bounds_audio_and_items_and_ignores_duplicate_acks(frames):
    window = PlaybackBufferWindow()
    capacity = min(4096, 32 * 24000 // frames)
    produced = []

    async def source():
        for index in range(capacity + 2):
            produced.append(index)
            yield {"type": "audio_chunk", "data": b"\x01\x00" * frames}

    stream = window.stream(source())
    try:
        ids = [(await anext(stream))["buffer_id"] for _ in range(capacity)]
        assert window.pending_count == capacity
        assert window.pending_seconds == pytest.approx(capacity * frames / 24000)
        assert window.pending_seconds <= 32
        assert len(produced) == capacity
        next_buffer = asyncio.create_task(anext(stream))
        for invalid in (True, str(ids[0]), ids[-1] + 1000):
            window.acknowledge(invalid)
        await asyncio.sleep(0)
        assert not next_buffer.done()
        window.acknowledge(ids[0])
        window.acknowledge(ids[0])
        event = await asyncio.wait_for(next_buffer, timeout=1)
        assert event["buffer_id"] > ids[-1]
        assert window.pending_count == capacity
        assert window.pending_seconds == pytest.approx(capacity * frames / 24000)
    finally:
        await stream.aclose()
        window.clear()
    assert window.pending_count == 0
    assert window.pending_seconds == 0


async def test_default_window_buffers_seconds_instead_of_packet_count():
    window = PlaybackBufferWindow()

    async def source():
        for _ in range(1601):
            yield {"type": "audio_chunk", "data": b"\x01\x00" * 480}

    stream = window.stream(source())
    try:
        for _ in range(1600):
            await anext(stream)
        assert window.pending_count == 1600
        assert window.pending_seconds == pytest.approx(32)
    finally:
        await stream.aclose()
