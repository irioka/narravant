"""Text-to-Speech (TTS) stream service for audiobook generation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol

import httpx

from narravant.core.settings import Settings
from narravant.services.playback import PlannedUtterance

logger = logging.getLogger(__name__)

TRANSIENT_TTS_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_SCENE_PAUSE_DURATION_MS = 3000

def _extract_audio_chunks(sse_data_payload: str) -> Iterator[bytes]:
    """Decode base64 PCM audio from one Gemini SSE `data:` payload (JSON)."""
    try:
        parsed = json.loads(sse_data_payload)
    except json.JSONDecodeError:
        return
    for candidate in parsed.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                try:
                    yield base64.b64decode(inline["data"])
                except (ValueError, TypeError):
                    continue


class TtsError(Exception):
    """Exception raised when speech synthesis fails."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TtsClient(Protocol):
    """Protocol for speech synthesis."""

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model: str | None = None,
        style: str = "",
    ) -> AsyncIterator[bytes]:
        """Synthesize text into raw audio (PCM) chunks.

        `style` is an optional performance direction sent to Gemini TTS via the
        part's speechMetadata.style field (not read aloud).
        """
        ...


class GeminiTtsClient:
    """Production client calling Google Gemini TTS (gemini-3.8-flash-tts).

    🔑 NOTE: Invoking real Gemini TTS generates external API costs and requires
    explicit user approval.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-3.8-flash-tts",
        language_code: str = "ja",
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._language_code = language_code
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model: str | None = None,
        style: str = "",
    ) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise TtsError("GEMINI_API_KEY is not configured.")

        selected_model = model or self._default_model
        # A custom Voice Design ID (voice_...) is referenced via
        # speechConfig.voiceConfig.voice in the REST API. The typed google-genai
        # SDK VoiceConfig (2.x) does NOT expose that `voice` field (only
        # prebuilt_voice_config / replicated_voice_config) and rejects designed
        # voice IDs, so we call the REST streamGenerateContent (SSE) endpoint
        # directly. Verified output: audio/l16; rate=24000; channels=1
        # (16-bit PCM, 24 kHz mono).
        #
        # Performance direction is passed as the documented turn-level styling
        # field parts[].speechMetadata.style (verified field name), NOT prepended
        # into the spoken text, so the direction is followed but not read aloud.
        part: dict[str, Any] = {"text": text}
        if style:
            part["speechMetadata"] = {"style": style}
        url = f"{self._base_url}/models/{selected_model}:streamGenerateContent"
        body = {
            "contents": [{"parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"voice": voice_id},
                    "languageCode": self._language_code,
                },
            },
        }
        last_error: TtsError | None = None
        for attempt in range(1, self._max_attempts + 1):
            received_audio = False
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    async with client.stream(
                        "POST", url, params={"key": self._api_key, "alt": "sse"}, json=body
                    ) as response:
                        if response.status_code != 200:
                            detail = (await response.aread()).decode("utf-8", "replace")
                            raise TtsError(
                                f"Gemini TTS failed ({response.status_code}): {detail[:500]}",
                                retryable=response.status_code in TRANSIENT_TTS_STATUS_CODES,
                            )
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:") :].strip()
                            if not payload:
                                continue
                            for chunk in _extract_audio_chunks(payload):
                                received_audio = True
                                yield chunk
                        return
            except TtsError as exc:
                if not exc.retryable or received_audio or attempt >= self._max_attempts:
                    raise
                last_error = exc
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if received_audio or attempt >= self._max_attempts:
                    raise TtsError(f"Network error calling Gemini TTS: {exc}") from exc
                last_error = TtsError(f"Network error calling Gemini TTS: {exc}", retryable=True)
            except httpx.RequestError as exc:
                raise TtsError(f"Network error calling Gemini TTS: {exc}") from exc
            except Exception as exc:
                raise TtsError(f"Gemini TTS streaming failed: {exc}") from exc

            logger.warning(
                "Transient Gemini TTS failure; retrying attempt=%d/%d",
                attempt + 1,
                self._max_attempts,
            )
            if self._retry_backoff_seconds:
                await asyncio.sleep(self._retry_backoff_seconds * attempt)

        raise last_error or TtsError("Gemini TTS failed after retrying")


class FakeTtsClient:
    """Deterministic fake TTS client for local testing without incurring API costs."""

    def __init__(
        self,
        chunk_size: int = 1024,
        fail_on_utterance_indices: set[int] | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.call_count = 0
        self.synthesized_utterances: list[tuple[str, str]] = []
        self.fail_on_utterance_text: str | None = None
        self.fail_on_utterance_indices = set(fail_on_utterance_indices) if fail_on_utterance_indices else set()

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [{"text": text, "voice_id": voice_id} for text, voice_id in self.synthesized_utterances]

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model: str | None = None,
        style: str = "",
    ) -> AsyncIterator[bytes]:
        idx = self.call_count
        self.call_count += 1
        self.synthesized_utterances.append((text, voice_id))
        self.last_style = style

        if idx in self.fail_on_utterance_indices:
            raise TtsError(f"Simulated TTS failure on utterance index {idx}")

        if self.fail_on_utterance_text and self.fail_on_utterance_text in text:
            raise TtsError(f"Simulated TTS failure for text: {text}")

        # Deterministic 16-bit PCM bytes
        h = hashlib.sha256(f"{voice_id}:{text}".encode()).digest()
        part1 = h * (self.chunk_size // 32)
        part2 = (h[::-1]) * (self.chunk_size // 32)

        yield part1
        await asyncio.sleep(0.01)  # allow task interruption/switching
        yield part2


class TtsPlaybackCoordinator:
    """Coordinates speech synthesis across a sequence of planned utterances.

    Handles sequentially yielding events, error halting, and cancellation.
    """

    def __init__(self, tts_client: TtsClient, scene_pause_duration_ms: int = DEFAULT_SCENE_PAUSE_DURATION_MS) -> None:
        self.tts_client = tts_client
        self.scene_pause_duration_ms = max(0, scene_pause_duration_ms)
        self._stopped = False
        self._current_task: asyncio.Task | None = None

    def stop(self) -> None:
        """Signal immediate stop and cancel any ongoing synthesis task."""
        self._stopped = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    async def run_stream(
        self,
        utterances: list[PlannedUtterance],
    ) -> AsyncIterator[dict[str, Any]]:
        """Run sequential synthesis and yield playback events."""
        self._stopped = False
        self._current_task = asyncio.current_task()

        previous_scene: int | None = None
        for u in utterances:
            if self._stopped:
                break

            if previous_scene is not None and u.scene_number != previous_scene:
                yield {
                    "type": "scene_pause",
                    "scene_number": u.scene_number,
                    "duration_ms": self.scene_pause_duration_ms,
                }

            yield {
                "type": "utterance_start",
                "scene_number": u.scene_number,
                "utterance_index": u.utterance_index,
                "speaker": u.speaker,
                "target_type": u.target_type,
            }

            try:
                # The Fountain parenthetical is a performance direction sent to
                # Gemini TTS via speechMetadata.style (turn-level styling), so it
                # guides delivery but is not read aloud.
                stream = self.tts_client.synthesize_stream(
                    text=u.text,
                    voice_id=u.voice_id,
                    style=u.performance_direction,
                )
                async for chunk in stream:
                    if self._stopped:
                        break
                    yield {
                        "type": "audio_chunk",
                        "scene_number": u.scene_number,
                        "utterance_index": u.utterance_index,
                        "data": chunk,
                    }
            except asyncio.CancelledError:
                self._stopped = True
                return
            except Exception as exc:
                yield {
                    "type": "error",
                    "scene_number": u.scene_number,
                    "utterance_index": u.utterance_index,
                    "message": str(exc),
                }
                return

            if self._stopped:
                break

            yield {
                "type": "utterance_end",
                "scene_number": u.scene_number,
                "utterance_index": u.utterance_index,
            }
            previous_scene = u.scene_number

        if not self._stopped:
            yield {"type": "playback_complete"}


def create_tts_client(settings: Settings) -> TtsClient:
    """Factory creating TtsClient from application settings."""
    return GeminiTtsClient(
        api_key=settings.gemini_api_key or "",
        default_model=settings.gemini_tts_model,
        timeout_seconds=getattr(settings, "gemini_connection_timeout_seconds", 120),
        max_attempts=getattr(
            settings,
            "playback_tts_max_attempts",
            getattr(settings, "gemini_generation_max_attempts", 3),
        ),
        retry_backoff_seconds=getattr(settings, "gemini_retry_backoff_seconds", 1),
    )
