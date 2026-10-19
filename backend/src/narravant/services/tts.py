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
from narravant.services.speech_segments import (
    SegmentVoice,
    SpeechSegment,
    build_speech_segments,
)
from narravant.services.voice_models import canonical_voice_id

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
    """Protocol for speech synthesis with segment and legacy entry points."""

    async def synthesize_segment_stream(
        self,
        segment: SpeechSegment,
        model: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Synthesize one sentence segment into raw PCM chunks."""
        ...

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model: str | None = None,
        style: str = "",
    ) -> AsyncIterator[bytes]:
        """Synthesize one legacy single-speaker request through the segment builder."""
        ...


def _normalize_tts_voice_id(voice_id: str) -> str:
    """Translate a legacy resource name to the canonical synthesis ID."""
    if voice_id.startswith(("voice_", "voices/")):
        return canonical_voice_id(voice_id)
    return voice_id


def build_tts_request(segment: SpeechSegment, model: str, language_code: str) -> dict[str, Any]:
    """Build the REST body for one supported Gemini sentence segment."""
    if segment.listener is not None:
        raise TtsError(
            "Gemini TTS では、割当済みカスタム声による二話者の重なり再生は未対応です。"
        )

    part: dict[str, Any] = {"text": segment.text}
    if segment.style:
        part["speechMetadata"] = {"style": segment.style}
    voice_configuration: dict[str, Any] = {
        "voiceConfig": {"voice": _normalize_tts_voice_id(segment.primary.voice_id)}
    }

    # The selected model is carried by the REST endpoint path, not this JSON body.
    _ = model
    return {
        "contents": [{"parts": [part]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {**voice_configuration, "languageCode": language_code},
        },
    }


class GeminiTtsClient:
    """Gemini TTS の streamGenerateContent raw PCM 経路を使う client。"""

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
        """既存の単一話者呼出を一つの SpeechSegment に変換する。"""
        try:
            normalized_voice_id = _normalize_tts_voice_id(voice_id)
        except ValueError as exc:
            raise TtsError("Invalid custom voice ID") from exc
        segment = SpeechSegment(
            text=text,
            primary=SegmentVoice("speaker", normalized_voice_id),
            listener=None,
            style=style,
        )
        async for chunk in self.synthesize_segment_stream(segment, model=model):
            yield chunk

    async def synthesize_segment_stream(
        self,
        segment: SpeechSegment,
        model: str | None = None,
    ) -> AsyncIterator[bytes]:
        """一つの本文 segment を raw PCM chunk として逐次取得する。"""
        selected_model = model or self._default_model
        try:
            body = build_tts_request(segment, selected_model, self._language_code)
        except ValueError as exc:
            raise TtsError("Invalid custom voice ID") from exc
        if not self._api_key:
            raise TtsError("GEMINI_API_KEY is not configured.")

        url = f"{self._base_url}/models/{selected_model}:streamGenerateContent"
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
        self.synthesized_styles: list[str] = []
        self.synthesized_segments: list[SpeechSegment] = []
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
        try:
            normalized_voice_id = _normalize_tts_voice_id(voice_id)
        except ValueError as exc:
            raise TtsError("Invalid custom voice ID") from exc
        segment = SpeechSegment(text, SegmentVoice("speaker", normalized_voice_id), None, style)
        async for chunk in self.synthesize_segment_stream(segment, model=model):
            yield chunk

    async def synthesize_segment_stream(
        self,
        segment: SpeechSegment,
        model: str | None = None,
    ) -> AsyncIterator[bytes]:
        try:
            primary_voice_id = _normalize_tts_voice_id(segment.primary.voice_id)
            listener_voice_id = (
                _normalize_tts_voice_id(segment.listener.voice_id) if segment.listener is not None else None
            )
        except ValueError as exc:
            raise TtsError("Invalid custom voice ID") from exc

        idx = self.call_count
        self.call_count += 1
        self.synthesized_utterances.append((segment.text, primary_voice_id))
        self.synthesized_styles.append(segment.style)
        self.synthesized_segments.append(segment)
        self.last_style = segment.style

        if idx in self.fail_on_utterance_indices:
            raise TtsError(f"Simulated TTS failure on utterance index {idx}")
        if self.fail_on_utterance_text and self.fail_on_utterance_text in segment.text:
            raise TtsError(f"Simulated TTS failure for text: {segment.text}")

        speaker_ids = f"{primary_voice_id}:{listener_voice_id or ''}"
        digest = hashlib.sha256(f"{speaker_ids}:{segment.text}".encode()).digest()
        part1 = digest * (self.chunk_size // 32)
        part2 = digest[::-1] * (self.chunk_size // 32)

        yield part1
        await asyncio.sleep(0.01)
        yield part2


class TtsPlaybackCoordinator:
    """Coordinates sentence-level synthesis while preserving scene transitions."""

    def __init__(
        self,
        tts_client: TtsClient,
        scene_pause_duration_ms: int = DEFAULT_SCENE_PAUSE_DURATION_MS,
    ) -> None:
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
        for utterance in utterances:
            if self._stopped:
                break

            if previous_scene is not None and utterance.scene_number != previous_scene:
                yield {
                    "type": "scene_pause",
                    "scene_number": utterance.scene_number,
                    "duration_ms": self.scene_pause_duration_ms,
                }

            yield {
                "type": "utterance_start",
                "scene_number": utterance.scene_number,
                "utterance_index": utterance.utterance_index,
                "speaker": utterance.speaker,
                "target_type": utterance.target_type,
            }

            try:
                for segment in build_speech_segments(utterance):
                    stream = self.tts_client.synthesize_segment_stream(segment)
                    async for chunk in stream:
                        if self._stopped:
                            break
                        yield {
                            "type": "audio_chunk",
                            "scene_number": utterance.scene_number,
                            "utterance_index": utterance.utterance_index,
                            "data": chunk,
                        }
                    if self._stopped:
                        break
                    yield {
                        "type": "audio_segment_end",
                        "scene_number": utterance.scene_number,
                        "utterance_index": utterance.utterance_index,
                    }
            except asyncio.CancelledError:
                self._stopped = True
                return
            except Exception as exc:
                yield {
                    "type": "error",
                    "scene_number": utterance.scene_number,
                    "utterance_index": utterance.utterance_index,
                    "message": str(exc),
                }
                return

            if self._stopped:
                break

            yield {
                "type": "utterance_end",
                "scene_number": utterance.scene_number,
                "utterance_index": utterance.utterance_index,
            }
            previous_scene = utterance.scene_number

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
