"""再生側が消費した分だけ次の音声を生成・送信する、接続単位の制御。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

PLAYBACK_BUFFER_SECONDS = 32
PLAYBACK_MAX_BUFFER_ITEMS = 4096
PLAYBACK_SAMPLE_RATE = 24000
MAX_PCM_BUFFER_BYTES = 48000
MAX_PAUSE_BUFFER_MS = 1000


class PlaybackBufferWindow:
    def __init__(self) -> None:
        self._pending: dict[int, int] = {}
        self._pending_frames = 0
        self._next_id = 0
        self._available = asyncio.Event()
        self._available.set()

    @property
    def max_items(self) -> int:
        return PLAYBACK_MAX_BUFFER_ITEMS

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending_seconds(self) -> float:
        return self._pending_frames / PLAYBACK_SAMPLE_RATE

    def acknowledge(self, buffer_id: object) -> None:
        # bool・文字列・重複・未発行・前の再生のACKで枠を増やさない。
        if type(buffer_id) is int and buffer_id in self._pending:
            self._pending_frames -= self._pending.pop(buffer_id)
            self._available.set()

    def clear(self) -> None:
        self._pending.clear()
        self._pending_frames = 0
        self._available.set()

    async def _reserve(self, frames: int) -> int:
        limit = PLAYBACK_SAMPLE_RATE * PLAYBACK_BUFFER_SECONDS
        while len(self._pending) >= self.max_items or self._pending_frames + frames > limit:
            self._available.clear()
            await self._available.wait()
        self._next_id += 1
        self._pending[self._next_id] = frames
        self._pending_frames += frames
        if len(self._pending) >= self.max_items or self._pending_frames >= limit:
            self._available.clear()
        return self._next_id

    async def stream(
        self, events: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[dict[str, Any], None]:
        pending_byte = b""
        async with aclosing(events):
            while True:
                # packet数でなく音声の秒数を確保してから、次の生成取得を待つ。
                await self._available.wait()
                event = await anext(events, None)
                if event is None:
                    return
                kind = event["type"]
                if kind == "audio_chunk":
                    data = pending_byte + event["data"]
                    playable_length = len(data) - len(data) % 2
                    pending_byte = data[playable_length:]
                    for offset in range(0, playable_length, MAX_PCM_BUFFER_BYTES):
                        chunk = data[offset:min(offset + MAX_PCM_BUFFER_BYTES, playable_length)]
                        buffer_id = await self._reserve(len(chunk) // 2)
                        yield {**event, "data": chunk, "buffer_id": buffer_id}
                elif kind in {"scene_pause", "utterance_pause"}:
                    remaining = event["duration_ms"]
                    while True:
                        duration = min(remaining, MAX_PAUSE_BUFFER_MS)
                        frames = max(1, round(PLAYBACK_SAMPLE_RATE * duration / 1000))
                        buffer_id = await self._reserve(frames)
                        yield {**event, "duration_ms": duration, "buffer_id": buffer_id}
                        remaining -= duration
                        if remaining <= 0:
                            break
                else:
                    if pending_byte and kind in {"audio_segment_end", "utterance_end", "playback_complete"}:
                        raise ValueError("incomplete_pcm_sample")
                    yield event
