"""Voice Design service providing voice persona creation via Google Gemini API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from narravant.core.settings import Settings
from narravant.services.voice_design_errors import (
    VoiceDesignFailureReason,
    classify_voice_design_failure,
    safe_provider_code,
    safe_provider_status,
)
from narravant.services.voice_models import (
    ProviderVoice,
    VoicePage,
    canonical_voice_id,
    parse_provider_datetime,
)

logger = logging.getLogger(__name__)


# Transient upstream failures that are safe to retry: a rejected (non-200)
# response has not created a stored, billable voice, so re-issuing the request
# cannot duplicate a successful creation. 503/UNAVAILABLE is a temporary
# provider outage; 502/504 are gateway-level hiccups in front of the provider.
_TRANSIENT_RETRY_STATUSES = frozenset({502, 503, 504})


class VoiceDesignError(Exception):
    """Exception raised when voice design creation fails."""

    def __init__(
        self,
        message: str,
        *,
        provider_status: int | None = None,
        provider_code: str | None = None,
        reason: VoiceDesignFailureReason | None = None,
        attempt: int = 1,
    ) -> None:
        super().__init__(message)
        self.provider_status = provider_status
        self.provider_code = provider_code
        self.attempt = attempt
        self.reason = reason or (
            "invalid_response"
            if provider_code == "INVALID_RESPONSE"
            else classify_voice_design_failure(provider_status, provider_code)
        )


class VoiceDesignClient(Protocol):
    """Protocol for managing stored custom voices."""

    async def create_voice(
        self,
        speaker: str,
        voice_traits: str,
        language_code: str = "ja-JP",
        display_name: str | None = None,
    ) -> ProviderVoice:
        """Create a prompted voice and return provider metadata."""
        ...

    async def list_voices(self, page_token: str | None = None) -> VoicePage:
        """Fetch one page of stored custom voices."""
        ...

    async def get_voice(self, voice_id: str) -> ProviderVoice:
        """Retrieve one stored custom voice."""
        ...

    async def delete_voice(self, voice_id: str) -> None:
        """Delete one stored custom voice without retrying the request."""
        ...


def _provider_voice_from_payload(
    data: Any,
    *,
    default_type: str | None = None,
) -> ProviderVoice:
    """Parse only allowlisted provider metadata; never retain keys or sample audio."""
    if not isinstance(data, dict):
        raise VoiceDesignError(
            "Gemini Voices API returned an invalid response",
            provider_code="INVALID_RESPONSE",
        )
    raw_id = data.get("id") or data.get("voice_id") or data.get("name")
    if not isinstance(raw_id, str):
        raise VoiceDesignError(
            "Gemini Voices API response did not contain a voice ID",
            provider_code="INVALID_RESPONSE",
        )
    try:
        voice_id = canonical_voice_id(raw_id)
        expire_time = parse_provider_datetime(data.get("expire_time", data.get("expireTime")))
    except ValueError as exc:
        raise VoiceDesignError(
            "Gemini Voices API returned invalid voice metadata",
            provider_code="INVALID_RESPONSE",
        ) from exc

    raw_type = data.get("type", default_type)
    if not isinstance(raw_type, str):
        raw_type = default_type or ""
    voice_type = raw_type.lower().removeprefix("voice_type_")
    if voice_type not in {"prompted", "replicated"}:
        raise VoiceDesignError(
            "Gemini Voices API returned an unsupported voice type",
            provider_code="INVALID_RESPONSE",
        )
    raw_language = data.get("language_code", data.get("languageCode"))
    language_code = raw_language if isinstance(raw_language, str) else None
    raw_display_name = data.get("display_name", data.get("displayName"))
    display_name = raw_display_name if isinstance(raw_display_name, str) else None
    return ProviderVoice(
        voice_id=voice_id,
        voice_type=voice_type,
        language_code=language_code,
        expire_time=expire_time,
        display_name=display_name,
    )


def _voice_request_error(operation: str, response: httpx.Response) -> VoiceDesignError:
    provider_code = _provider_error_code(response)
    logger.warning(
        "Gemini Voices API %s rejected status=%s provider_code=%s",
        operation,
        response.status_code,
        provider_code or "UNKNOWN",
    )
    return VoiceDesignError(
        f"Gemini Voices API {operation} request was rejected",
        provider_status=response.status_code,
        provider_code=provider_code,
    )


class GeminiVoiceDesignClient:
    """Production client calling Google Gemini Voice Design API (POST /v1beta/voices)."""

    def __init__(
        self,
        api_key: str,
        tts_model: str = "gemini-3.8-flash-tts",
        timeout_seconds: float = 300.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ):
        self._api_key = api_key
        self._tts_model = tts_model
        self._timeout_seconds = timeout_seconds
        # Total attempts for a transient failure (1 initial call + retries).
        self._max_attempts = max(1, max_attempts)
        # Base seconds for the backoff; the wait grows in proportion to the
        # attempt number (retry_backoff_seconds * attempt), matching the TTS
        # client so both Gemini retry policies scale their wait the same way.
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._endpoint = "https://generativelanguage.googleapis.com/v1beta/voices"
        self._read_timeout_seconds = 30.0

    async def create_voice(
        self,
        speaker: str,
        voice_traits: str,
        language_code: str = "ja-JP",
        display_name: str | None = None,
    ) -> ProviderVoice:
        if not self._api_key:
            raise VoiceDesignError("GEMINI_API_KEY is not configured", reason="configuration", attempt=0)

        # Voice Design (prompted): store must be true. The display name labels
        # the voice by document and speaker; voice_traits stays a separate prompt.
        payload: dict[str, Any] = _voice_design_payload(
            voice_traits,
            model=self._tts_model,
            language_code=language_code,
            display_name=display_name,
        )

        attempt_count = 1
        try:
            # Voice Design waits for a complete persistent voice resource.  It can
            # legitimately exceed a short TTS-preview request, so use the shared
            # configured connection wait.  A network timeout could leave a voice
            # in an unknown state, so those are not retried; only rejected
            # (non-200) transient responses, which cannot have stored a voice,
            # are retried below.
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                # Retry transient upstream failures (503 UNAVAILABLE and gateway
                # 502/504) up to the configured number of attempts before
                # surfacing the error. These are temporary provider outages, and
                # a rejected response cannot have created a stored voice, so the
                # retry is safe and idempotent. The wait grows in proportion to
                # the attempt number, matching the TTS client.
                response = await client.post(self._endpoint, headers={"x-goog-api-key": self._api_key}, json=payload)
                for attempt in range(1, self._max_attempts):
                    if response.status_code not in _TRANSIENT_RETRY_STATUSES:
                        break
                    logger.warning(
                        "Gemini Voice Design transient failure status=%s attempt=%d/%d; retrying",
                        response.status_code,
                        attempt,
                        self._max_attempts,
                    )
                    if self._retry_backoff_seconds:
                        await asyncio.sleep(self._retry_backoff_seconds * attempt)
                    attempt_count += 1
                    response = await client.post(
                        self._endpoint, headers={"x-goog-api-key": self._api_key}, json=payload
                    )

                if response.status_code != 200:
                    provider_code = safe_provider_code(_provider_error_code(response))
                    raise VoiceDesignError(
                        "Gemini Voice Design request was rejected",
                        provider_status=response.status_code,
                        provider_code=provider_code,
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise VoiceDesignError(
                        "Gemini Voice Design returned an invalid response", reason="invalid_response"
                    ) from exc
                if not isinstance(data, dict):
                    raise VoiceDesignError(
                        "Gemini Voice Design returned an invalid response", reason="invalid_response"
                    )
                return _provider_voice_from_payload(data, default_type="prompted")
        except httpx.RequestError as exc:
            raise VoiceDesignError(
                "Gemini Voice Design creation result is unknown",
                provider_code="UNKNOWN",
                reason="transport_unknown",
                attempt=attempt_count,
            ) from exc
        except VoiceDesignError as exc:
            # 作成経路のみ診断値を正規化する。取得・削除の既存内部codeは維持する。
            raise VoiceDesignError(
                "Gemini Voice Design creation failed",
                provider_status=safe_provider_status(
                    exc.provider_status if exc.provider_status is not None else response.status_code
                ),
                provider_code=safe_provider_code(exc.provider_code),
                reason=exc.reason,
                attempt=attempt_count,
            ) from exc

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise VoiceDesignError("GEMINI_API_KEY is not configured. Please set GEMINI_API_KEY in .env")
        try:
            async with httpx.AsyncClient(timeout=self._read_timeout_seconds) as client:
                response = await client.get(
                    url,
                    headers={"x-goog-api-key": self._api_key},
                    params=params,
                )
                for attempt in range(1, self._max_attempts):
                    if response.status_code not in _TRANSIENT_RETRY_STATUSES:
                        break
                    logger.warning(
                        "Gemini Voices API GET transient failure status=%s attempt=%d/%d; retrying",
                        response.status_code,
                        attempt,
                        self._max_attempts,
                    )
                    if self._retry_backoff_seconds:
                        await asyncio.sleep(self._retry_backoff_seconds * attempt)
                    response = await client.get(
                        url,
                        headers={"x-goog-api-key": self._api_key},
                        params=params,
                    )
        except httpx.RequestError as exc:
            logger.warning("Gemini Voices API GET failed exception_type=%s", type(exc).__name__)
            raise VoiceDesignError(
                "Unable to reach Gemini Voices API",
                provider_code="NETWORK_ERROR",
            ) from exc

        if response.status_code != 200:
            raise _voice_request_error("GET", response)
        try:
            data = response.json()
        except ValueError as exc:
            raise VoiceDesignError(
                "Gemini Voices API returned an invalid response",
                provider_code="INVALID_RESPONSE",
            ) from exc
        if not isinstance(data, dict):
            raise VoiceDesignError(
                "Gemini Voices API returned an invalid response",
                provider_code="INVALID_RESPONSE",
            )
        return data

    async def list_voices(self, page_token: str | None = None) -> VoicePage:
        data = await self._get_json(
            self._endpoint,
            params={"pageSize": 100, **({"pageToken": page_token} if page_token else {})},
        )
        raw_voices = data.get("voices", [])
        if not isinstance(raw_voices, list):
            raise VoiceDesignError(
                "Gemini Voices API returned an invalid response",
                provider_code="INVALID_RESPONSE",
            )

        voices: list[ProviderVoice] = []
        for item in raw_voices:
            if not isinstance(item, dict):
                raise VoiceDesignError(
                    "Gemini Voices API returned an invalid response",
                    provider_code="INVALID_RESPONSE",
                )
            raw_type = item.get("type", "prompted")
            if isinstance(raw_type, str) and raw_type.lower().removeprefix("voice_type_") == "prebuilt":
                continue
            voices.append(_provider_voice_from_payload(item, default_type="prompted"))

        raw_token = data.get("nextPageToken", data.get("next_page_token"))
        next_page_token = raw_token if isinstance(raw_token, str) and raw_token else None
        return VoicePage(voices=tuple(voices), next_page_token=next_page_token)

    async def get_voice(self, voice_id: str) -> ProviderVoice:
        canonical_id = canonical_voice_id(voice_id)
        data = await self._get_json(f"{self._endpoint}/{canonical_id}")
        return _provider_voice_from_payload(data)

    async def delete_voice(self, voice_id: str) -> None:
        canonical_id = canonical_voice_id(voice_id)
        if not self._api_key:
            raise VoiceDesignError("GEMINI_API_KEY is not configured. Please set GEMINI_API_KEY in .env")
        try:
            async with httpx.AsyncClient(timeout=self._read_timeout_seconds) as client:
                response = await client.delete(
                    f"{self._endpoint}/{canonical_id}",
                    headers={"x-goog-api-key": self._api_key},
                )
        except httpx.RequestError as exc:
            logger.warning("Gemini Voices API DELETE failed exception_type=%s", type(exc).__name__)
            raise VoiceDesignError(
                "Unable to reach Gemini Voices API",
                provider_code="NETWORK_ERROR",
            ) from exc
        if response.status_code not in {200, 204}:
            raise _voice_request_error("DELETE", response)


def _provider_error_code(response: httpx.Response) -> str | None:
    """Extract only the provider's stable code, never an error body or input text."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("status")
    return code if isinstance(code, str) else None


def _voice_design_payload(
    voice_traits: str,
    *,
    model: str | None = None,
    language_code: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Build a prompted Voice Design request with optional fields when set."""
    voice: dict[str, Any] = {
        "type": "prompted",
        "prompted": {"input": voice_traits},
    }
    if model:
        voice["model"] = model
    if language_code:
        voice["language_code"] = language_code
    if display_name is not None:
        voice["display_name"] = display_name
    return {"store": True, "voice": voice}


class FakeVoiceDesignClient:
    """Deterministic fake client for custom voices without external API costs."""

    def __init__(self, prefix: str = "voice_fake_") -> None:
        self._prefix = prefix
        self.call_count = 0
        self.list_call_count = 0
        self.get_call_count = 0
        self.delete_call_count = 0
        self.list_page_size = 100
        self.last_traits: str | None = None
        self.last_speaker: str | None = None
        self.last_display_name: str | None = None
        self.should_fail = False
        self.voices: dict[str, ProviderVoice] = {}

    async def create_voice(
        self,
        speaker: str,
        voice_traits: str,
        language_code: str = "ja-JP",
        display_name: str | None = None,
    ) -> ProviderVoice:
        self.call_count += 1
        self.last_speaker = speaker
        self.last_traits = voice_traits
        self.last_display_name = display_name
        if self.should_fail:
            raise VoiceDesignError("Simulated Voice Design failure")

        traits_hash = hashlib.sha256(voice_traits.encode("utf-8")).hexdigest()[:8]
        voice_id = canonical_voice_id(f"{self._prefix}{self.call_count}_{traits_hash}")
        voice = ProviderVoice(
            voice_id=voice_id,
            voice_type="prompted",
            language_code=language_code,
            expire_time=parse_provider_datetime((datetime.now(UTC) + timedelta(days=365)).isoformat()),
            display_name=display_name,
        )
        self.voices[voice_id] = voice
        return voice

    async def list_voices(self, page_token: str | None = None) -> VoicePage:
        self.list_call_count += 1
        if self.should_fail:
            raise VoiceDesignError(
                "Simulated Voices API failure",
                provider_status=503,
                provider_code="UNAVAILABLE",
            )
        try:
            offset = int(page_token or "0")
        except ValueError as exc:
            raise VoiceDesignError(
                "Invalid fake page token",
                provider_code="INVALID_ARGUMENT",
            ) from exc
        ordered = sorted(self.voices.values(), key=lambda item: item.voice_id)
        end = offset + self.list_page_size
        next_token = str(end) if end < len(ordered) else None
        return VoicePage(tuple(ordered[offset:end]), next_token)

    async def get_voice(self, voice_id: str) -> ProviderVoice:
        self.get_call_count += 1
        if self.should_fail:
            raise VoiceDesignError(
                "Simulated Voices API failure",
                provider_status=503,
                provider_code="UNAVAILABLE",
            )
        canonical_id = canonical_voice_id(voice_id)
        try:
            return self.voices[canonical_id]
        except KeyError as exc:
            raise VoiceDesignError(
                "Voice was not found",
                provider_status=404,
                provider_code="NOT_FOUND",
            ) from exc

    async def delete_voice(self, voice_id: str) -> None:
        self.delete_call_count += 1
        if self.should_fail:
            raise VoiceDesignError(
                "Simulated Voices API failure",
                provider_status=503,
                provider_code="UNAVAILABLE",
            )
        canonical_id = canonical_voice_id(voice_id)
        if canonical_id not in self.voices:
            raise VoiceDesignError(
                "Voice was not found",
                provider_status=404,
                provider_code="NOT_FOUND",
            )
        del self.voices[canonical_id]


def create_voice_design_client(settings: Settings) -> VoiceDesignClient:
    """Factory creating VoiceDesignClient from application settings."""
    return GeminiVoiceDesignClient(
        api_key=settings.gemini_api_key or "",
        tts_model=settings.gemini_tts_model,
        timeout_seconds=settings.gemini_connection_timeout_seconds,
        max_attempts=getattr(
            settings,
            "gemini_voice_design_max_attempts",
            getattr(settings, "gemini_generation_max_attempts", 3),
        ),
        retry_backoff_seconds=getattr(settings, "gemini_retry_backoff_seconds", 1),
    )
