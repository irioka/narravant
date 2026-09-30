"""NARRAVANT FastAPI entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
    get_db_manager,
    get_storage_client,
)
from narravant.api.documents import router as documents_router
from narravant.api.emotion_arc import catalog_router as emotion_arc_catalog_router
from narravant.api.emotion_arc import router as emotion_arc_router
from narravant.api.errors import ApiErrorEnvelope, register_error_handlers
from narravant.api.playback import router as playback_router
from narravant.api.schemas import (
    ReanalysisTaskResultEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskErrorEvent,
    TaskProgressEvent,
)
from narravant.api.tasks import router as tasks_router
from narravant.api.voices import draft_router as draft_voices_router
from narravant.api.voices import router as voices_router
from narravant.core.settings import Settings
from narravant.core.valence_vector import ValenceVectorizer
from narravant.db.database import DatabaseManager, DocumentRepository, TaskRepository
from narravant.services.tasks import TaskManager
from narravant.storage.local import LocalScriptStorageClient

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_VERSION = (BACKEND_ROOT.parent / "VERSION").read_text(encoding="utf-8").strip()
QUIET_LOGGERS = (
    "asyncio",
    "charset_normalizer",
    "httpcore",
    "httpx",
    "urllib3",
    "uvicorn.access",
)
COMMON_ERROR_RESPONSES = {
    401: {"model": ApiErrorEnvelope},
    403: {"model": ApiErrorEnvelope},
    404: {"model": ApiErrorEnvelope},
    409: {"model": ApiErrorEnvelope},
    422: {"model": ApiErrorEnvelope},
    500: {"model": ApiErrorEnvelope},
    503: {"model": ApiErrorEnvelope},
}


def configure_runtime_logging(log_level: str) -> None:
    """Configure product diagnostics without exposing transport-level chatter."""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("narravant").setLevel(log_level)
    for logger_name in QUIET_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown events."""
    # Explicit test dependency injection intentionally bypasses production startup wiring.
    if get_db_manager not in app.dependency_overrides or get_storage_client not in app.dependency_overrides:
        try:
            load_dotenv(BACKEND_ROOT.parent / ".env", override=False)
            settings = Settings.load(BACKEND_ROOT)
            configure_runtime_logging(settings.log_level)
            logger.info("Application logging configured level=%s", settings.log_level)
            db_manager = DatabaseManager(db_path=str(settings.sqlite_db_path))
            db_manager.init_schema()
            storage_client = LocalScriptStorageClient(base_dir=settings.local_storage_path)
            vectorizer = ValenceVectorizer(settings.valence_vectorization)
            _rebuild_current_valence_index(DocumentRepository(db_manager), storage_client, vectorizer)
            DocumentRepository(db_manager).ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
            TaskManager(
                TaskRepository(db_manager),
                settings.sse_poll_interval_milliseconds / 1000,
            ).recover_interrupted()
            app.state.settings = settings
            app.state.db_manager = db_manager
            app.state.storage_client = storage_client
        except Exception:
            logger.critical("Critical runtime dependency initialization failed", exc_info=True)
            raise
    yield


def _rebuild_current_valence_index(
    repository: DocumentRepository,
    storage: LocalScriptStorageClient,
    vectorizer: ValenceVectorizer,
) -> None:
    """Rebuild only derived 10D data; canonical local documents remain untouched."""
    entries: list[tuple[str, int, list[float]]] = []
    for document_id, version_id in repository.list_current_document_versions():
        try:
            payload, _ = storage.read_structured_script(document_id, version_id)
            valence = payload.get("emotion_arc", {}).get("valence")
            if isinstance(valence, list) and valence:
                entries.append((document_id, version_id, vectorizer.vectorize(valence)))
        except (FileNotFoundError, ValueError, TypeError):
            # A damaged document must not block startup or cause an empty write;
            # its normal detail endpoint will surface the canonical error.
            continue
    repository.replace_current_valence_vectors(entries)


app = FastAPI(
    title="NARRAVANT API",
    description="Intelligent screenplay analysis and narrative arc workbench API",
    version=APP_VERSION,
    lifespan=lifespan,
    responses=COMMON_ERROR_RESPONSES,
)

app.include_router(documents_router)
app.include_router(voices_router)
app.include_router(draft_voices_router)
app.include_router(playback_router)
app.include_router(emotion_arc_catalog_router)
app.include_router(emotion_arc_router)
app.include_router(tasks_router)
register_error_handlers(app)


def _schemas_compatible(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if existing == candidate:
        return True
    if existing.get("type") == candidate.get("type") and existing.get("title") == candidate.get("title"):
        existing_props = set(existing.get("properties", {}).keys())
        candidate_props = set(candidate.get("properties", {}).keys())
        if existing_props == candidate_props:
            existing_req = set(existing.get("required", []))
            candidate_req = set(candidate.get("required", []))
            if existing_req == candidate_req:
                return True
    return False


def _custom_openapi() -> dict:
    """Expose SSE event payload Pydantic schemas without pretending SSE is JSON."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    event_models = (
        TaskProgressEvent,
        ReanalysisTaskResultEvent,
        TaskCompletedEvent,
        TaskErrorEvent,
        TaskCancelledEvent,
    )
    for model in event_models:
        event_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        nested_models = event_schema.pop("$defs", {})
        for def_name, def_schema in nested_models.items():
            if def_name in components:
                if not _schemas_compatible(components[def_name], def_schema):
                    raise ValueError(f"Schema conflict for component {def_name}")
            else:
                components[def_name] = def_schema
        if model.__name__ in components and not _schemas_compatible(components[model.__name__], event_schema):
            raise ValueError(f"Schema conflict for component {model.__name__}")
        if model.__name__ not in components:
            components[model.__name__] = event_schema
    progress_response = schema["paths"]["/api/v1/tasks/{task_id}/progress"]["get"]["responses"]["200"]
    progress_response["description"] = "Persisted task SSE events; comments may be heartbeat frames."
    progress_response.setdefault("content", {})["text/event-stream"] = {
        "schema": {
            "oneOf": [
                {"$ref": f"#/components/schemas/{model.__name__}"}
                for model in (
                    TaskProgressEvent,
                    TaskCompletedEvent,
                    TaskErrorEvent,
                    TaskCancelledEvent,
                )
            ]
        }
    }
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "narravant"}


@app.get("/api/v1/version")
async def get_version() -> dict[str, str]:
    """API version endpoint."""
    return {"version": APP_VERSION, "name": "NARRAVANT"}
