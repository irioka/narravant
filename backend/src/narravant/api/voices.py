"""API endpoint for Voice Design (voice creation)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from narravant.api.authorization import authorize_document_write
from narravant.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_document_repo,
    get_tts_client,
    get_voice_design_client,
    get_voice_management_service,
    get_voice_registry_repo,
)
from narravant.api.errors import ApiError, ApiErrorCode, ApiErrorEnvelope
from narravant.api.voice_schemas import VoiceListResponse, VoiceSummary, VoiceUsageResponse
from narravant.db.database import DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.services.tts import TtsClient, TtsError
from narravant.services.voice_design import VoiceDesignClient, VoiceDesignError
from narravant.services.voice_management import VoiceManagementError, VoiceManagementService
from narravant.services.voice_models import StoredVoiceRecord, canonical_voice_id, utc_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["voices"])
draft_router = APIRouter(prefix="/api/v1/voices", tags=["voices"])
VOICE_API_ERROR_RESPONSES = {code: {"model": ApiErrorEnvelope} for code in (404, 422, 502, 503)}


class CreateVoiceRequest(BaseModel):
    """Request payload to create a new voice persona."""

    model_config = ConfigDict(extra="ignore")
    speaker: str = Field(min_length=1, description="Speaker name (narrator or character)")
    voice_traits: str = Field(min_length=1, description="Natural language voice description")
    language_code: str = Field(default="ja-JP", description="Language code")
    display_name: str | None = Field(default=None, description="Optional display name for the stored voice")


class CreateVoiceResponse(BaseModel):
    """Response payload containing created voice assignment details."""

    model_config = ConfigDict(extra="ignore")
    speaker: str
    voice_id: str
    voice_traits: str


class VoicePreviewRequest(BaseModel):
    """Request payload to synthesize a short sample line with an existing voice."""

    model_config = ConfigDict(extra="ignore")
    voice_id: str = Field(min_length=1, description="Created voice id (voice_...)")
    text: str = Field(min_length=1, max_length=500, description="Short sample line to synthesize")
    style: str = Field(default="", description="Optional performance direction")
    language_code: str = Field(default="ja-JP", description="Language code")


async def _create_voice(
    request: CreateVoiceRequest,
    voice_client: VoiceDesignClient,
    registry: VoiceRegistryRepository,
    owner_user_id: str,
    response: Response,
    owner_lock: asyncio.Lock | None = None,
) -> CreateVoiceResponse:
    """Create a voice without persisting an assignment."""
    try:
        created_voice = await voice_client.create_voice(
            speaker=request.speaker,
            voice_traits=request.voice_traits,
            language_code=request.language_code,
            display_name=request.display_name,
        )
        voice_id = canonical_voice_id(created_voice.voice_id)
    except VoiceDesignError as exc:
        # Voice quota exceeded: surface a distinct code so the UI can tell the
        # user to delete old voices (voice ids are version-managed and never
        # auto-deleted). Detected from the provider error text/status.
        message = str(exc)
        if _is_voice_quota_error(message, exc.provider_code):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="VOICE_LIMIT_EXCEEDED: 作成できる声の上限に達しました。不要な古い声を削除してください。",
            ) from exc
        # Transient provider outage (503 UNAVAILABLE / gateway hiccup / network):
        # not a client input or configuration problem. Surface a distinct 503 so
        # the UI can advise retrying later instead of telling the user to fix
        # their voice traits or API settings.
        if _is_transient_provider_error(exc.provider_status, exc.provider_code):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "VOICE_PROVIDER_UNAVAILABLE: "
                    "音声生成サービスが一時的に利用できません。時間をおいて再試行してください。"
                ),
            ) from exc
        provider_code = f" ({exc.provider_code})" if exc.provider_code else ""
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini Voice Design request failed{provider_code}.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini Voice Design returned an invalid voice ID.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error creating voice.",
        ) from exc

    if created_voice.voice_id != voice_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini Voice Design returned a non-canonical voice ID.",
        )

    created_at = utc_iso()
    record = StoredVoiceRecord(
        owner_user_id=owner_user_id,
        voice_id=voice_id,
        speaker_label=request.speaker,
        voice_traits=request.voice_traits,
        language_code=request.language_code,
        created_at=created_at,
        created_at_source="local_response",
        registered_at=created_at,
        expire_time=utc_iso(created_voice.expire_time) if created_voice.expire_time else None,
        last_checked_at=created_at,
        availability="available",
    )

    def persist() -> None:
        registry.upsert(record)

    try:
        if owner_lock is None:
            persist()
        else:
            async with owner_lock:
                persist()
    except Exception as exc:
        # Provider creation already succeeded. Preserve the successful response
        # so the client never retries and creates a duplicate billable voice.
        logger.warning("Voice registry sync failed exception_type=%s", type(exc).__name__)
        response.headers["X-Voice-Registry-Sync"] = "pending"

    return CreateVoiceResponse(
        speaker=request.speaker,
        voice_id=voice_id,
        voice_traits=request.voice_traits,
    )


async def _preview_voice(
    request: VoicePreviewRequest,
    tts_client: TtsClient,
    voice_service: VoiceManagementService,
    owner_user_id: str,
    *,
    allow_current_assignment: bool,
) -> Response:
    """Validate a custom voice before synthesizing a saved or unsaved preview."""
    try:
        await voice_service.validate_voice_ids(
            owner_user_id,
            [request.voice_id],
            allow_current_assignment=allow_current_assignment,
        )
    except VoiceManagementError as exc:
        _raise_voice_management_http_error(exc)
    try:
        chunks: list[bytes] = []
        async for chunk in tts_client.synthesize_stream(
            text=request.text,
            voice_id=request.voice_id,
            style=request.style,
        ):
            chunks.append(chunk)
    except TtsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return Response(
        content=b"".join(chunks),
        media_type="audio/l16; rate=24000; channels=1",
    )


@router.post(
    "/{document_id}/voices",
    response_model=CreateVoiceResponse,
    status_code=status.HTTP_200_OK,
)
async def create_document_voice(
    document_id: str,
    request: CreateVoiceRequest,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    voice_client: Annotated[VoiceDesignClient, Depends(get_voice_design_client)],
    registry: Annotated[VoiceRegistryRepository, Depends(get_voice_registry_repo)],
    voice_service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    response: Response,
) -> CreateVoiceResponse:
    """Create a custom voice persona using Gemini Voice Design.

    NOTE (MF-2): This endpoint calls Voice Design and returns the created voice_id.
    It does NOT persist to the document or advance version_id. Persistence of
    voice_assignments is handled via regular PUT /api/v1/documents/{doc_id}.
    """
    # 1. Authorize document existence
    authorize_document_write(repo, document_id, user)

    # 2. Return the created voice without advancing document version.
    return await _create_voice(
        request,
        voice_client,
        registry,
        user.user_id,
        response,
        voice_service.lock_for_owner(user.user_id),
    )


@draft_router.post(
    "",
    response_model=CreateVoiceResponse,
    status_code=status.HTTP_200_OK,
)
async def create_draft_voice(
    request: CreateVoiceRequest,
    voice_client: Annotated[VoiceDesignClient, Depends(get_voice_design_client)],
    registry: Annotated[VoiceRegistryRepository, Depends(get_voice_registry_repo)],
    voice_service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    response: Response,
) -> CreateVoiceResponse:
    """Create a voice for an unsaved import draft without creating a document."""
    return await _create_voice(
        request,
        voice_client,
        registry,
        user.user_id,
        response,
        voice_service.lock_for_owner(user.user_id),
    )


def _is_transient_provider_error(
    provider_status: int | None,
    provider_code: str | None = None,
) -> bool:
    """Detect a temporary provider outage that the user should simply retry."""
    if provider_code in {"UNAVAILABLE", "NETWORK_ERROR", "DEADLINE_EXCEEDED"}:
        return True
    return provider_status in {502, 503, 504}


def _is_voice_quota_error(message: str, provider_code: str | None = None) -> bool:
    """Heuristically detect a voice-count/quota limit error from the provider."""
    if provider_code == "RESOURCE_EXHAUSTED":
        return True
    lowered = message.lower()
    quota_terms = ("quota", "limit", "exceeded", "too many", "resource_exhausted", "429")
    voice_terms = ("voice", "voices")
    return "resource_exhausted" in lowered or (
        any(q in lowered for q in quota_terms) and any(v in lowered for v in voice_terms)
    )


@router.post(
    "/{document_id}/voices/preview",
    status_code=status.HTTP_200_OK,
)
async def preview_document_voice(
    document_id: str,
    request: VoicePreviewRequest,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    tts_client: Annotated[TtsClient, Depends(get_tts_client)],
    voice_service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Synthesize a short sample line with an existing voice for the check button.

    Returns raw PCM audio (audio/l16; rate=24000; channels=1). The sample line is
    not persisted; it is used only to preview a created voice.
    """
    authorize_document_write(repo, document_id, user)
    return await _preview_voice(request, tts_client, voice_service, user.user_id, allow_current_assignment=True)


@draft_router.post(
    "/preview",
    status_code=status.HTTP_200_OK,
)
async def preview_draft_voice(
    request: VoicePreviewRequest,
    tts_client: Annotated[TtsClient, Depends(get_tts_client)],
    voice_service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Preview a voice assigned only in an unsaved import draft."""
    return await _preview_voice(request, tts_client, voice_service, user.user_id, allow_current_assignment=False)


def _raise_voice_management_http_error(error: VoiceManagementError) -> None:
    raise ApiError(error.status_code, ApiErrorCode(error.code), error.message) from error


@draft_router.get(
    "",
    response_model=VoiceListResponse,
    responses={code: schema for code, schema in VOICE_API_ERROR_RESPONSES.items() if code in {502, 503}},
)
async def list_stored_voices(
    service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> VoiceListResponse:
    """Return the current user's reconciled custom voice library."""
    try:
        return await service.list_voices(user.user_id)
    except VoiceManagementError as exc:
        _raise_voice_management_http_error(exc)


@draft_router.get(
    "/{voice_id}/usages",
    response_model=VoiceUsageResponse,
    responses={code: schema for code, schema in VOICE_API_ERROR_RESPONSES.items() if code in {404, 422, 503}},
)
async def get_stored_voice_usages(
    voice_id: str,
    service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> VoiceUsageResponse:
    """Return current-version document assignments that use this voice."""
    try:
        return await service.get_usages(user.user_id, voice_id)
    except VoiceManagementError as exc:
        _raise_voice_management_http_error(exc)


@draft_router.get(
    "/{voice_id}",
    response_model=VoiceSummary,
    responses=VOICE_API_ERROR_RESPONSES,
)
async def get_stored_voice(
    voice_id: str,
    service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> VoiceSummary:
    """Refresh and return one known custom voice's provider state."""
    try:
        return await service.get_voice(user.user_id, voice_id)
    except VoiceManagementError as exc:
        _raise_voice_management_http_error(exc)


@draft_router.delete(
    "/{voice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={code: schema for code, schema in VOICE_API_ERROR_RESPONSES.items() if code in {404, 422, 502, 503}},
)
async def delete_stored_voice(
    voice_id: str,
    service: Annotated[VoiceManagementService, Depends(get_voice_management_service)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Delete a known custom voice without modifying any document payload."""
    try:
        await service.delete_voice(user.user_id, voice_id)
    except VoiceManagementError as exc:
        _raise_voice_management_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
