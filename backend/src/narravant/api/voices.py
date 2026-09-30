"""API endpoint for Voice Design (voice creation)."""

from __future__ import annotations

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
)
from narravant.db.database import DocumentRepository
from narravant.services.tts import TtsClient, TtsError
from narravant.services.voice_design import VoiceDesignClient, VoiceDesignError

router = APIRouter(prefix="/api/v1/documents", tags=["voices"])
draft_router = APIRouter(prefix="/api/v1/voices", tags=["voices"])


class CreateVoiceRequest(BaseModel):
    """Request payload to create a new voice persona."""

    model_config = ConfigDict(extra="ignore")
    speaker: str = Field(min_length=1, description="Speaker name (narrator or character)")
    voice_traits: str = Field(min_length=1, description="Natural language voice description")
    language_code: str = Field(default="ja-JP", description="Language code")


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
) -> CreateVoiceResponse:
    """Create a voice without persisting an assignment."""
    try:
        voice_id = await voice_client.create_voice(
            speaker=request.speaker,
            voice_traits=request.voice_traits,
            language_code=request.language_code,
        )
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
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error creating voice.",
        ) from exc

    return CreateVoiceResponse(
        speaker=request.speaker,
        voice_id=voice_id,
        voice_traits=request.voice_traits,
    )


async def _preview_voice(request: VoicePreviewRequest, tts_client: TtsClient) -> Response:
    """Synthesize a non-persistent sample for a saved or unsaved draft."""
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
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CreateVoiceResponse:
    """Create a custom voice persona using Gemini Voice Design.

    NOTE (MF-2): This endpoint calls Voice Design and returns the created voice_id.
    It does NOT persist to the document or advance version_id. Persistence of
    voice_assignments is handled via regular PUT /api/v1/documents/{doc_id}.
    """
    # 1. Authorize document existence
    authorize_document_write(repo, document_id, user)

    # 2. Return the created voice without advancing document version.
    return await _create_voice(request, voice_client)


@draft_router.post(
    "",
    response_model=CreateVoiceResponse,
    status_code=status.HTTP_200_OK,
)
async def create_draft_voice(
    request: CreateVoiceRequest,
    voice_client: Annotated[VoiceDesignClient, Depends(get_voice_design_client)],
    _: Annotated[CurrentUser, Depends(get_current_user)],
) -> CreateVoiceResponse:
    """Create a voice for an unsaved import draft without creating a document."""
    return await _create_voice(request, voice_client)


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
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Synthesize a short sample line with an existing voice for the check button.

    Returns raw PCM audio (audio/l16; rate=24000; channels=1). The sample line is
    not persisted; it is used only to preview a created voice.
    """
    authorize_document_write(repo, document_id, user)
    return await _preview_voice(request, tts_client)


@draft_router.post(
    "/preview",
    status_code=status.HTTP_200_OK,
)
async def preview_draft_voice(
    request: VoicePreviewRequest,
    tts_client: Annotated[TtsClient, Depends(get_tts_client)],
    _: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Preview a voice assigned only in an unsaved import draft."""
    return await _preview_voice(request, tts_client)
