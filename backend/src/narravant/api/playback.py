"""WebSocket endpoint for real-time audiobook speech playback streaming."""

from __future__ import annotations

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
)
from narravant.db.database import DocumentRepository
from narravant.services.playback import (
    PlaybackPlanError,
    build_playback_plan,
)
from narravant.services.tts import TtsPlaybackCoordinator, create_tts_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["playback"])


def _build_tts_client(settings):
    """Construct the TTS client for playback.

    Isolated as a module-level function so tests can monkeypatch it with a fake
    (WebSocket routes cannot use FastAPI dependency overrides for this).
    """
    return create_tts_client(settings)


@router.websocket("/{document_id}/playback")
async def document_playback_websocket(
    websocket: WebSocket,
    document_id: str,
) -> None:
    """Stream TTS playback events and PCM audio chunks over WebSocket.

    Messages:
    Client -> Server:
      - {"action": "start", "scene_number": <int>, "start_utterance_index": <int>}
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
    db_manager = websocket.app.state.db_manager
    storage = websocket.app.state.storage_client
    repo = DocumentRepository(db_manager)
    # Single local user (matches HTTP get_current_user behavior).
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    tts_client = _build_tts_client(settings)

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
    ) -> None:
        nonlocal coordinator
        try:
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

            action = msg.get("action")
            if action == "start":
                # Duplicate start while actively playing is ignored (SC-3)
                if active_task and not active_task.done():
                    continue

                start_scene = int(msg.get("scene_number") or 1)
                start_utterance = int(msg.get("start_utterance_index") or 0)
                active_task = asyncio.create_task(_execute_playback(start_scene, start_utterance))

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
