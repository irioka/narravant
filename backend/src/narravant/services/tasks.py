"""Persisted task state machine and replayable SSE event source."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from threading import RLock
from time import monotonic, time
from typing import Any

from narravant.db.database import TaskRepository
from narravant.domain.tasks import TERMINAL_TASK_STATUSES, TaskStateError, TaskStatus, validate_transition


@dataclass(frozen=True)
class PersistedTaskEvent:
    event_id: int
    event_type: str
    payload: dict[str, Any]


class EphemeralTaskEvents:
    """Process-local completed import payloads, deliberately excluded from SQLite replay."""

    def __init__(self) -> None:
        self._events: dict[str, tuple[PersistedTaskEvent, float]] = {}

    def put(self, task_id: str, event_id: int, payload: dict[str, Any], ttl_seconds: int) -> None:
        self._events[task_id] = (PersistedTaskEvent(event_id, "completed", payload), monotonic() + ttl_seconds)

    def after(self, task_id: str, event_id: int) -> list[PersistedTaskEvent]:
        event = self._live_event(task_id)
        return [event] if event is not None and event.event_id > event_id else []

    def has(self, task_id: str) -> bool:
        return self._live_event(task_id) is not None

    def _live_event(self, task_id: str) -> PersistedTaskEvent | None:
        item = self._events.get(task_id)
        if item is None:
            return None
        event, expires_at = item
        if monotonic() >= expires_at:
            del self._events[task_id]
            return None
        return event


ephemeral_task_events = EphemeralTaskEvents()


class TaskManager:
    def __init__(
        self,
        repository: TaskRepository,
        poll_interval_seconds: float = 0.25,
        ephemeral_events: EphemeralTaskEvents | None = None,
    ) -> None:
        self.repository = repository
        self.poll_interval_seconds = poll_interval_seconds
        self.ephemeral_events = ephemeral_events or ephemeral_task_events
        self._import_completion_lock = RLock()

    def create(self, owner_user_id: str, task_type: str, document_id: str | None, payload: dict[str, Any]) -> str:
        task_id = str(uuid.uuid4())
        self.repository.create(task_id, owner_user_id, task_type, document_id, payload)
        self.publish(task_id, "progress", {"phase": "queued", "percentage": 0, "message": "処理を開始します。"})
        return task_id

    def start(self, task_id: str) -> None:
        self._transition(task_id, TaskStatus.RUNNING)

    def request_cancel(self, task_id: str) -> None:
        self._transition(task_id, TaskStatus.CANCEL_REQUESTED)
        self.publish(
            task_id,
            "progress",
            {"phase": "cancelling", "percentage": 0, "message": "キャンセルを要求しました。"},
        )

    def complete(self, task_id: str, payload: dict[str, Any]) -> None:
        self._transition(task_id, TaskStatus.COMPLETED)
        self.publish(task_id, "completed", payload)

    def complete_import_draft(self, task_id: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        """Complete an import while retaining its body only in volatile process memory."""
        with self._import_completion_lock:
            self._transition(task_id, TaskStatus.COMPLETED)
            persisted = self.events_after(task_id, 0)
            next_event_id = (persisted[-1].event_id if persisted else 0) + 1
            self.ephemeral_events.put(task_id, next_event_id, payload, ttl_seconds)

    def complete_after_publication(self, task_id: str, payload: dict[str, Any]) -> None:
        """Record completion when cancellation races with an already-published document.

        Workers must check cancellation before publishing.  Once SQLite points at a
        newly created immutable GCS version, deleting it would violate the canonical
        document invariant; completion therefore wins only in that narrow window.
        """
        task = self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        current = TaskStatus(task["status"])
        if current is TaskStatus.RUNNING:
            self.complete(task_id, payload)
            return
        if current is TaskStatus.CANCEL_REQUESTED and self.repository.transition(
            task_id, TaskStatus.CANCEL_REQUESTED.value, TaskStatus.COMPLETED.value
        ):
            self.publish(task_id, "completed", payload)
            return
        raise TaskStateError(f"Cannot complete published task from {current}")

    def fail(self, task_id: str, payload: dict[str, Any]) -> None:
        self._transition(task_id, TaskStatus.FAILED)
        self.publish(task_id, "error", payload)

    def cancel(self, task_id: str, message: str) -> None:
        self._transition(task_id, TaskStatus.CANCELLED)
        self.publish(task_id, "cancelled", {"message": message})

    def recover_interrupted(self) -> list[str]:
        task_ids = self.repository.fail_interrupted()
        for task_id in task_ids:
            self.publish(
                task_id,
                "error",
                {"code": "PROCESS_RESTARTED", "message": "サーバー再起動のため処理を中断しました。", "retryable": True},
            )
        return task_ids

    def publish(self, task_id: str, event_type: str, payload: dict[str, Any]) -> int:
        return self.repository.append_event(task_id, event_type, payload)

    def events_after(self, task_id: str, last_event_id: int) -> list[PersistedTaskEvent]:
        persisted = [
            PersistedTaskEvent(row["event_id"], row["event_type"], json.loads(row["payload_json"]))
            for row in self.repository.events_after(task_id, last_event_id)
        ]
        return [
            *persisted,
            *self.ephemeral_events.after(task_id, persisted[-1].event_id if persisted else last_event_id),
        ]

    async def stream(
        self,
        task_id: str,
        last_event_id: int,
        heartbeat_seconds: float,
        expires_at_epoch_seconds: int | None = None,
        clock: Callable[[], float] = time,
    ) -> AsyncIterator[str]:
        cursor = last_event_id
        last_activity = monotonic()
        while True:
            with self._import_completion_lock:
                events = self.events_after(task_id, cursor)
                task = self.repository.get(task_id)
                completed_import_without_payload = (
                    task is not None
                    and TaskStatus(task["status"]) is TaskStatus.COMPLETED
                    and task["task_type"] == "document_import"
                    and not self.ephemeral_events.has(task_id)
                )
            for event in events:
                cursor = event.event_id
                last_activity = monotonic()
                yield self._format_event(event)
            if task is None or TaskStatus(task["status"]) in TERMINAL_TASK_STATUSES:
                if completed_import_without_payload:
                    yield self._format_event(
                        PersistedTaskEvent(
                            cursor + 1,
                            "error",
                            {
                                "code": "PROCESS_RESTARTED",
                                "message": "サーバー再起動またはdraft有効期限切れのため、Importを再実行してください。",
                                "retryable": True,
                            },
                        )
                    )
                return
            if expires_at_epoch_seconds is not None and clock() >= expires_at_epoch_seconds:
                return
            if monotonic() - last_activity >= heartbeat_seconds:
                last_activity = monotonic()
                yield ": heartbeat\n\n"
            await asyncio.sleep(self.poll_interval_seconds)

    def _transition(self, task_id: str, target: TaskStatus) -> None:
        task = self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        current = TaskStatus(task["status"])
        validate_transition(current, target)
        if not self.repository.transition(task_id, current.value, target.value):
            raise TaskStateError(f"Concurrent task state change for {task_id}")

    @staticmethod
    def _format_event(event: PersistedTaskEvent) -> str:
        payload = json.dumps(event.payload, ensure_ascii=False)
        return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n"
