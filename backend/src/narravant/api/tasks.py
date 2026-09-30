"""Replayable task progress SSE and explicit cancellation API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from narravant.api.authorization import task_not_found
from narravant.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_settings,
    get_task_manager,
)
from narravant.api.schemas import TaskCancelResponse
from narravant.core.settings import Settings
from narravant.domain.tasks import TERMINAL_TASK_STATUSES, TaskStateError, TaskStatus
from narravant.services.tasks import TaskManager

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _owned_task(manager: TaskManager, task_id: str, user: CurrentUser) -> dict[str, str]:
    task = manager.repository.get(task_id)
    if task is None or (task.get("owner_user_id") and task["owner_user_id"] != user.user_id):
        raise task_not_found()
    return task


@router.get("/{task_id}/progress")
async def task_progress(
    task_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    manager: Annotated[TaskManager, Depends(get_task_manager)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    _owned_task(manager, task_id, user)
    try:
        cursor = int(last_event_id or "0")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid Last-Event-ID") from exc
    return StreamingResponse(
        manager.stream(
            task_id,
            cursor,
            settings.sse_heartbeat_interval_seconds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{task_id}/cancel", response_model=TaskCancelResponse, status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(
    task_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    manager: Annotated[TaskManager, Depends(get_task_manager)],
) -> TaskCancelResponse:
    task = _owned_task(manager, task_id, user)
    if TaskStatus(task["status"]) in TERMINAL_TASK_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is already terminal")
    try:
        manager.request_cancel(task_id)
    except TaskStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TaskCancelResponse(task_id=task_id)
