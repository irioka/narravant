"""WebSocket endpoint for real-time audiobook speech playback streaming."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
)
from narravant.api.schemas import VoiceAssignmentSchema
from narravant.db.database import DocumentRepository
from narravant.services.playback import (
    PlaybackPlanError,
    build_playback_plan,
)
from narravant.services.tts import TtsPlaybackCoordinator, create_tts_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["playback"])


class DraftPlaybackRequest(BaseModel):
    """保存せず試聴する下書きの、再生に必要な情報だけを受け取る。"""

    model_config = ConfigDict(extra="forbid")
    source_fountain: str = Field(min_length=1)
    voice_assignments: list[VoiceAssignmentSchema]


class PlaybackStartRequest(BaseModel):
    """WebSocket の開始メッセージを音声生成前に検証する。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["start"]
    scene_number: int = Field(default=1, ge=1, strict=True)
    start_utterance_index: int = Field(default=0, ge=0, strict=True)
    draft: DraftPlaybackRequest | None = None


def _build_tts_client(settings):
    """Construct the TTS client for playback.

    Isolated as a module-level function so tests can monkeypatch it with a fake
    (WebSocket routes cannot use FastAPI dependency overrides for this).
    """
    return create_tts_client(settings)


@router.websocket("/playback")
@router.websocket("/{document_id}/playback")
async def document_playback_websocket(
    websocket: WebSocket,
    document_id: str | None = None,
) -> None:
    """Stream TTS playback events and PCM audio chunks over WebSocket.

    Messages:
    Client -> Server:
      - {"action": "start", "scene_number": <int>, "start_utterance_index": <int>}
      - draft route: start also includes {"draft": {"source_fountain": <str>, "voice_assignments": <list>}}
      - {"action": "stop"}
    Server -> Client:
      - {"event": "utterance_start", "scene_number": n, "utterance_index": i, "speaker": "...", "target_type": "..."}
      - {"event": "scene_pause", "scene_number": n, "duration_ms": <configured milliseconds>}
      - {"event": "audio_chunk", "scene_number": n, "utterance_index": i, "data": "<base64 encoded PCM>"}
      - {"event": "utterance_end", "scene_number": n, "utterance_index": i}
      - {"event": "playback_complete"}
      - {"event": "error", "scene_number": n, "utterance_index": i, "message": "..."}
    """
    await websocket.accept()

    # WebSocket routes cannot use the HTTP `Request`-based dependency accessors,
    # so resolve connection-scoped resources directly from app.state here.
    settings = websocket.app.state.settings
    tts_client = _build_tts_client(settings)

    doc = None
    storage = None
    # 未保存の再生は、この接続の下書きだけを使い、DB・ストレージに触れない。
    if document_id is not None:
        db_manager = websocket.app.state.db_manager
        storage = websocket.app.state.storage_client
        repo = DocumentRepository(db_manager)
        # Single local user (matches HTTP get_current_user behavior).
        repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
        doc = repo.get_document(document_id)
        if not doc:
            await websocket.send_json(
                {
                    "event": "error",
                    "message": f"Document '{document_id}' not found",
                    "scene_number": None,
                    "utterance_index": None,
                }
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    coordinator: TtsPlaybackCoordinator | None = None
    active_task: asyncio.Task | None = None

    async def _execute_playback(
        start_scene: int,
        start_utterance_index: int,
        draft: DraftPlaybackRequest | None,
    ) -> None:
        nonlocal coordinator
        try:
            if draft is not None:
                data = draft.model_dump()
            else:
                # Re-read fresh document version for playback
                current_version = doc["current_version_id"]
                data, _ = storage.read_structured_script(document_id, current_version)

            plan = build_playback_plan(
                data,
                start_scene=start_scene,
                start_utterance_index=start_utterance_index,
            )

            coordinator = TtsPlaybackCoordinator(
                tts_client=tts_client,
                scene_pause_duration_ms=getattr(settings, "playback_scene_pause_duration_ms", 3000),
            )
            async for ev in coordinator.run_stream(plan.utterances):
                ev_type = ev["type"]
                if ev_type == "utterance_start":
                    await websocket.send_json(
                        {
                            "event": "utterance_start",
                            "scene_number": ev["scene_number"],
                            "utterance_index": ev["utterance_index"],
                            "speaker": ev["speaker"],
                            "target_type": ev["target_type"],
                        }
                    )
                elif ev_type == "scene_pause":
                    await websocket.send_json(
                        {
                            "event": "scene_pause",
                            "scene_number": ev["scene_number"],
                            "duration_ms": ev["duration_ms"],
                        }
                    )
                elif ev_type == "audio_chunk":
                    b64_data = base64.b64encode(ev["data"]).decode("ascii")
                    await websocket.send_json(
                        {
                            "event": "audio_chunk",
                            "scene_number": ev["scene_number"],
                            "utterance_index": ev["utterance_index"],
                            "data": b64_data,
                        }
                    )
                elif ev_type == "utterance_end":
                    await websocket.send_json(
                        {
                            "event": "utterance_end",
                            "scene_number": ev["scene_number"],
                            "utterance_index": ev["utterance_index"],
                        }
                    )
                elif ev_type == "error":
                    await websocket.send_json(
                        {
                            "event": "error",
                            "scene_number": ev["scene_number"],
                            "utterance_index": ev["utterance_index"],
                            "message": ev["message"],
                        }
                    )
                elif ev_type == "playback_complete":
                    await websocket.send_json(
                        {
                            "event": "playback_complete",
                        }
                    )
        except PlaybackPlanError as plan_err:
            await websocket.send_json(
                {
                    "event": "error",
                    "scene_number": plan_err.scene_number,
                    "utterance_index": plan_err.utterance_index,
                    "message": plan_err.message,
                }
            )
        except asyncio.CancelledError:
            # Normal cancellation upon stop or disconnect
            pass
        except Exception as exc:
            logger.error("Playback streaming error: %s", exc, exc_info=True)
            await websocket.send_json(
                {
                    "event": "error",
                    "scene_number": None,
                    "utterance_index": None,
                    "message": f"Playback stream error: {exc}",
                }
            )

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                continue

            action = msg.get("action") if isinstance(msg, dict) else None
            if action == "start":
                # Duplicate start while actively playing is ignored (SC-3)
                if active_task and not active_task.done():
                    continue

                try:
                    request = PlaybackStartRequest.model_validate(msg)
                    if (document_id is None) != (request.draft is not None):
                        raise ValueError("下書き再生には draft が必要です")
                except (ValidationError, ValueError):
                    await websocket.send_json(
                        {
                            "event": "error",
                            "code": "INVALID_PLAYBACK_REQUEST",
                            "message": "Invalid playback request",
                            "scene_number": None,
                            "utterance_index": None,
                        }
                    )
                    continue
                active_task = asyncio.create_task(
                    _execute_playback(
                        request.scene_number,
                        request.start_utterance_index,
                        request.draft,
                    )
                )

            elif action == "stop":
                if coordinator:
                    coordinator.stop()
                if active_task and not active_task.done():
                    active_task.cancel()
                    active_task = None

    except WebSocketDisconnect:
        # Cost control (SC-1): guarantee immediate cancellation of background synthesis
        if coordinator:
            coordinator.stop()
        if active_task and not active_task.done():
            active_task.cancel()
