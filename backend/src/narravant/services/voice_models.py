"""Internal typed models for custom voice provider and registry data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

VoiceAvailability = Literal["available", "missing", "unknown"]
VoiceCreatedAtSource = Literal["local_response", "unknown"]
VoiceExpiryState = Literal["valid", "expiring", "expired", "stale", "unknown"]

_VOICE_ID_PATTERN = re.compile(r"voice_[A-Za-z0-9_-]+\Z")


def canonical_voice_id(value: str) -> str:
    """Return the canonical custom voice ID or reject unsafe resource names."""
    candidate = value
    if candidate.startswith("voices/"):
        candidate = candidate.removeprefix("voices/")
    if not _VOICE_ID_PATTERN.fullmatch(candidate):
        raise ValueError("Invalid custom voice ID")
    return candidate


def parse_provider_datetime(value: object) -> datetime | None:
    """Parse a provider RFC 3339 timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid provider timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_iso(value: datetime | None = None) -> str:
    """Format a timestamp as an ISO 8601 UTC string."""
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat()


@dataclass(frozen=True)
class ProviderVoice:
    """Allowlisted metadata returned for one custom provider voice."""

    voice_id: str
    voice_type: str
    language_code: str | None
    expire_time: datetime | None
    display_name: str | None = None


@dataclass(frozen=True)
class VoicePage:
    """One provider list page; aggregation belongs to voice management."""

    voices: tuple[ProviderVoice, ...]
    next_page_token: str | None


@dataclass(frozen=True)
class StoredVoiceRecord:
    """Owner-scoped metadata cached locally for a custom voice."""

    owner_user_id: str
    voice_id: str
    speaker_label: str | None
    voice_traits: str | None
    language_code: str | None
    created_at: str | None
    created_at_source: VoiceCreatedAtSource
    registered_at: str
    expire_time: str | None
    last_checked_at: str | None
    availability: VoiceAvailability
