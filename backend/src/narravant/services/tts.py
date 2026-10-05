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
DEFAULT_INTER_UTTERANCE_PAUSE_DURATION_MS = 1000
MAX_TTS_SEGMENT_CHARACTERS = 180
_SENTENCE_ENDINGS = frozenset("。！？!?…")
_CLAUSE_ENDINGS = frozenset("、，,;；:")
_CLOSING_PUNCTUATION = frozenset("」』）)]}’”\"〉》")
_NON_TERMINAL_ABBREVIATIONS = frozenset({"dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "jr.", "e.g.", "i.e."})


def _split_tts_text(text: str) -> list[str]:
    """計画上の発話を文単位の TTS 要求へ分割する。

    長文は句読点を優先して分ける。近くに句読点がない場合は、最初の音声を
    遅らせないよう空白または文字境界で分ける。句読点は直前の segment に残す。
    """
    text = text.strip()
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        sentence_end = character in _SENTENCE_ENDINGS
        if character == ".":
            previous = text[index - 1] if index > 0 else ""
            following = text[index + 1] if index + 1 < len(text) else ""
            # 小数点や e.g. のような略記のピリオドでは分割しない。
            preceding_token = text[: index + 1].rsplit(None, 1)[-1].lower()
            dotted_abbreviation = preceding_token in _NON_TERMINAL_ABBREVIATIONS
            sentence_end = (
                not dotted_abbreviation
                and not (previous.isdigit() and following.isdigit())
                and (not following or following.isspace() or following in _CLOSING_PUNCTUATION)
            )

        if not sentence_end:
            index += 1
            continue

        end = index + 1
        while end < len(text) and (text[end] in _SENTENCE_ENDINGS or text[end] in _CLOSING_PUNCTUATION):
            end += 1
        sentences.append(text[start:end].strip())
        start = end
        index = end

    if start < len(text):
        sentences.append(text[start:].strip())

    segments: list[str] = []
    for sentence in sentences:
        remaining = sentence
        while len(remaining) > MAX_TTS_SEGMENT_CHARACTERS:
            limit = MAX_TTS_SEGMENT_CHARACTERS
            clause_positions = [
                position for position, char in enumerate(remaining[:limit]) if char in _CLAUSE_ENDINGS
            ]
            if clause_positions:
                end = clause_positions[-1] + 1
            else:
                next_clause = next(
                    (
                        position + 1
                        for position, char in enumerate(remaining[limit:], start=limit)
                        if char in _CLAUSE_ENDINGS
                    ),
                    None,
                )
                if next_clause is not None and next_clause <= limit + 30:
                    end = next_clause
                else:
                    whitespace_positions = [
                        position for position, char in enumerate(remaining[:limit]) if char.isspace()
                    ]
                    end = whitespace_positions[-1] + 1 if whitespace_positions else limit

            piece = remaining[:end].strip()
            if piece:
                segments.append(piece)
            remaining = remaining[end:].strip()

        if remaining:
            segments.append(remaining)

    return segments


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
        self.synthesized_styles: list[str] = []
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
        self.synthesized_styles.append(style)
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

    def __init__(
        self,
        tts_client: TtsClient,
        scene_pause_duration_ms: int = DEFAULT_SCENE_PAUSE_DURATION_MS,
        inter_utterance_pause_duration_ms: int = DEFAULT_INTER_UTTERANCE_PAUSE_DURATION_MS,
    ) -> None:
        self.tts_client = tts_client
        self.scene_pause_duration_ms = max(0, scene_pause_duration_ms)
        self.inter_utterance_pause_duration_ms = max(0, inter_utterance_pause_duration_ms)
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

            if previous_scene is not None:
                if u.scene_number != previous_scene:
                    yield {
                        "type": "scene_pause",
                        "scene_number": u.scene_number,
                        "duration_ms": self.scene_pause_duration_ms,
                    }
                else:
                    yield {
                        "type": "utterance_pause",
                        "scene_number": u.scene_number,
                        "duration_ms": self.inter_utterance_pause_duration_ms,
                    }

            yield {
                "type": "utterance_start",
                "scene_number": u.scene_number,
                "utterance_index": u.utterance_index,
                "speaker": u.speaker,
                "target_type": u.target_type,
            }

            try:
                for segment_index, segment in enumerate(_split_tts_text(u.text)):
                    if segment_index > 0:
                        yield {
                            "type": "utterance_pause",
                            "scene_number": u.scene_number,
                            "duration_ms": self.inter_utterance_pause_duration_ms,
                        }
                    # Fountain の parenthetical は演技指示として送り、読み上げない。
                    # 分割後も同じ計画上の発話として扱い、ハイライトと再開位置を保つ。
                    stream = self.tts_client.synthesize_stream(
                        text=segment,
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
                    if self._stopped:
                        break
                    yield {
                        "type": "audio_segment_end",
                        "scene_number": u.scene_number,
                        "utterance_index": u.utterance_index,
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
