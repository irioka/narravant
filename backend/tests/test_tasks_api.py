"""Task state machine and persisted-event contract tests."""

from threading import Event, Thread, Timer

import pytest

from narravant.db.database import DatabaseManager, TaskRepository


@pytest.mark.asyncio
async def test_non_terminal_task_stream_stops_at_actual_token_expiry() -> None:
    from narravant.services.tasks import TaskManager

    class Repository:
        def events_after(self, task_id: str, cursor: int):
            return []

        def get(self, task_id: str):
            return {"status": "running"}

    clock_values = iter((99.0, 100.0))
    manager = TaskManager(Repository(), poll_interval_seconds=0)
    frames = [
        frame
        async for frame in manager.stream(
            "task-1",
            0,
            heartbeat_seconds=15,
            expires_at_epoch_seconds=100,
            clock=lambda: next(clock_values),
        )
    ]
    assert frames == []


def test_task_missing_and_not_owned_have_identical_404() -> None:
    from narravant.api.dependencies import CurrentUser
    from narravant.api.errors import ApiError, ApiErrorCode
    from narravant.api.tasks import _owned_task

    class Repository:
        def get(self, task_id: str):
            if task_id == "owned-by-other":
                return {"owner_user_id": "other"}
            return None

    manager = type("Manager", (), {"repository": Repository()})()
    errors = []
    for task_id in ("owned-by-other", "missing"):
        with pytest.raises(ApiError) as caught:
            _owned_task(manager, task_id, CurrentUser("user", "user@example.test"))
        errors.append(caught.value)
    assert [(error.status_code, error.code, error.message, error.context) for error in errors] == [
        (404, ApiErrorCode.TASK_NOT_FOUND, "指定されたタスクが見つかりません。", None),
        (404, ApiErrorCode.TASK_NOT_FOUND, "指定されたタスクが見つかりません。", None),
    ]


def test_task_transitions_reject_terminal_restart_and_worker_owned_cancellation() -> None:
    from narravant.domain.tasks import TaskStateError, TaskStatus, validate_transition

    validate_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    validate_transition(TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)
    validate_transition(TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELLED)

    with pytest.raises(TaskStateError):
        validate_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    with pytest.raises(TaskStateError):
        validate_transition(TaskStatus.QUEUED, TaskStatus.CANCELLED)


def test_latest_v1_schema_contains_persisted_task_and_monotonic_event_tables() -> None:
    db = DatabaseManager(":memory:")
    db.init_schema()
    with db.session() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"background_tasks", "task_events"} <= tables


def test_task_events_are_persisted_with_monotonic_ids() -> None:
    db = DatabaseManager(":memory:")
    db.init_schema()
    with db.session() as conn:
        now = "2026-09-09T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("u-1", "u-1@example.test", "Test", now, now),
        )
    tasks = TaskRepository(db)
    tasks.create("task-1", "u-1", "upload", None, {"filename": "draft.fountain"})
    assert tasks.append_event("task-1", "progress", {"percentage": 10}) == 1
    assert tasks.append_event("task-1", "progress", {"percentage": 20}) == 2
    assert [event["event_id"] for event in tasks.events_after("task-1", 1)] == [2]


def test_task_manager_replays_events_and_requires_explicit_cancel() -> None:
    from narravant.services.tasks import TaskManager

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = TaskRepository(db)
    with db.session() as conn:
        now = "2026-09-09T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("u-1", "u-1@example.test", "Test", now, now),
        )
    manager = TaskManager(repo)
    task_id = manager.create("u-1", "upload", None, {})
    manager.start(task_id)
    manager.publish(task_id, "progress", {"percentage": 50})
    manager.request_cancel(task_id)

    assert [event.event_id for event in manager.events_after(task_id, 1)] == [2, 3]
    assert repo.get(task_id)["status"] == "cancel_requested"


@pytest.mark.asyncio
async def test_import_completion_stream_emits_completed_event_when_payload_registration_is_in_progress() -> None:
    """A successful import must not turn into PROCESS_RESTARTED during completion."""
    from narravant.services.tasks import EphemeralTaskEvents, TaskManager

    class DelayedEphemeralEvents(EphemeralTaskEvents):
        def __init__(self) -> None:
            super().__init__()
            self.registration_started = Event()
            self.allow_registration = Event()

        def put(self, task_id: str, event_id: int, payload: dict[str, object], ttl_seconds: int) -> None:
            self.registration_started.set()
            assert self.allow_registration.wait(timeout=1)
            super().put(task_id, event_id, payload, ttl_seconds)

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = TaskRepository(db)
    with db.session() as conn:
        now = "2026-09-09T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("u-1", "u-1@example.test", "Test", now, now),
        )
    events = DelayedEphemeralEvents()
    manager = TaskManager(repo, poll_interval_seconds=0, ephemeral_events=events)
    task_id = manager.create("u-1", "document_import", None, {})
    manager.start(task_id)

    worker = Thread(
        target=manager.complete_import_draft,
        args=(task_id, {"import_draft": {"document_id": "draft-1"}}, 60),
    )
    worker.start()
    assert events.registration_started.wait(timeout=1)
    release = Timer(0.01, events.allow_registration.set)
    release.start()
    frames = [frame async for frame in manager.stream(task_id, 1, heartbeat_seconds=15)]
    release.join()
    worker.join()

    assert len(frames) == 1
    assert "event: completed\n" in frames[0]
    assert '"import_draft"' in frames[0]


def test_published_document_completion_wins_a_cancellation_race_without_losing_sse_terminal_event() -> None:
    from narravant.services.tasks import TaskManager

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = TaskRepository(db)
    with db.session() as conn:
        now = "2026-09-09T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("u-1", "u-1@example.test", "Test", now, now),
        )
    manager = TaskManager(repo)
    task_id = manager.create("u-1", "upload", "doc-1", {})
    manager.start(task_id)
    manager.request_cancel(task_id)

    manager.complete_after_publication(task_id, {"document_id": "doc-1", "version_id": 1})

    assert repo.get(task_id)["status"] == "completed"
    assert manager.events_after(task_id, 0)[-1].event_type == "completed"


def test_startup_recovery_marks_inflight_task_failed_and_persists_retryable_error() -> None:
    from narravant.services.tasks import TaskManager

    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = TaskRepository(db)
    with db.session() as conn:
        now = "2026-09-09T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("u-1", "u-1@example.test", "Test", now, now),
        )
    manager = TaskManager(repo)
    task_id = manager.create("u-1", "upload", None, {})
    manager.start(task_id)

    assert manager.recover_interrupted() == [task_id]
    assert repo.get(task_id)["status"] == "failed"
    assert manager.events_after(task_id, 1)[-1].payload["code"] == "PROCESS_RESTARTED"


def test_openapi_exposes_all_task_sse_payload_schemas() -> None:
    from narravant.main import app

    openapi = app.openapi()
    event_schema = openapi["paths"]["/api/v1/tasks/{task_id}/progress"]["get"]["responses"]["200"]["content"][
        "text/event-stream"
    ]["schema"]
    references = {item["$ref"] for item in event_schema["oneOf"]}

    assert references == {
        "#/components/schemas/TaskProgressEvent",
        "#/components/schemas/TaskCompletedEvent",
        "#/components/schemas/TaskErrorEvent",
        "#/components/schemas/TaskCancelledEvent",
    }


def test_openapi_canonical_tp_and_sse_schema_references_exist() -> None:
    from narravant.main import app

    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]

    analysis_schema = schemas["AnalysisSchema"]
    tp_items = analysis_schema["properties"]["turning_points"]["items"]
    assert "oneOf" in tp_items
    assert tp_items["discriminator"]["propertyName"] == "availability"
    refs = [item["$ref"] for item in tp_items["oneOf"]]
    assert refs == [
        "#/components/schemas/IdentifiedTurningPointSchema",
        "#/components/schemas/NotApplicableTurningPointSchema",
    ]

    event_schema = openapi["paths"]["/api/v1/tasks/{task_id}/progress"]["get"]["responses"]["200"]["content"][
        "text/event-stream"
    ]["schema"]
    for event_ref in event_schema["oneOf"]:
        schema_name = event_ref["$ref"].split("/")[-1]
        assert schema_name in schemas
        event_obj = schemas[schema_name]
        for prop in event_obj.get("properties", {}).values():
            if "$ref" in prop:
                ref_name = prop["$ref"].split("/")[-1]
                assert ref_name in schemas, f"Missing schema for ref {prop['$ref']}"


def test_openapi_rejects_conflicting_schema_definitions() -> None:
    from narravant.main import _schemas_compatible

    existing = {
        "type": "object",
        "title": "Model",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    conflicting = {
        "type": "object",
        "title": "Model",
        "properties": {"b": {"type": "integer"}},
        "required": ["b"],
    }
    assert not _schemas_compatible(existing, conflicting)
