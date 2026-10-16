"""Public response models for cross-document voice management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from narravant.services.voice_models import VoiceAvailability, VoiceCreatedAtSource, VoiceExpiryState


class VoiceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str
    display_name: str | None = None
    speaker_label: str | None
    voice_traits: str | None
    language_code: str | None
    created_at: str | None
    created_at_source: VoiceCreatedAtSource
    expire_time: str | None
    availability: VoiceAvailability
    expiry_state: VoiceExpiryState
    last_checked_at: str | None
    usage_count: int | None


class VoiceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voices: list[VoiceSummary]
    checked_at: str | None
    complete: bool


class VoiceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    speaker: str


class VoiceUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usages: list[VoiceUsage]
    complete: bool
