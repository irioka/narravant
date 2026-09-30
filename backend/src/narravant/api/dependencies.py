"""FastAPI runtime dependency accessors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from narravant.core.settings import Settings
from narravant.db.database import (
    DatabaseManager,
    DocumentRepository,
    TaskRepository,
)
from narravant.services.ingestion import ImportDraftStore
from narravant.services.narrative_adaptation import (
    NarrativeAdaptationConfig,
    NarrativeAdaptationService,
)
from narravant.services.tasks import TaskManager
from narravant.services.vertex_analysis import VertexDocumentAnalyzer
from narravant.storage.gcs import ScriptStorageClient

if TYPE_CHECKING:
    from narravant.services.tts import TtsClient
    from narravant.services.voice_design import VoiceDesignClient

LOCAL_USER_ID = "local-user"
LOCAL_USER_EMAIL = "local@narravant.local"
LOCAL_USER_DISPLAY_NAME = "Local User"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    display_name: str = ""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_manager(request: Request) -> DatabaseManager:
    return request.app.state.db_manager


def get_storage_client(request: Request) -> ScriptStorageClient:
    return request.app.state.storage_client


# NOTE: WebSocket routes (e.g. api/playback.py) cannot use the HTTP
# `Request`-based accessors above, since FastAPI injects a `WebSocket` on WS
# connections. The playback route resolves app.state directly from its
# `WebSocket` object instead of via these dependencies.


def get_document_repo(
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> DocumentRepository:
    return DocumentRepository(db)


def get_task_repo(
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> TaskRepository:
    return TaskRepository(db)


def get_task_manager(
    repository: Annotated[TaskRepository, Depends(get_task_repo)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskManager:
    return TaskManager(repository, settings.sse_poll_interval_milliseconds / 1000)


def get_import_draft_store(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImportDraftStore:
    """Keep pre-Save import content in process memory only."""
    store = getattr(request.app.state, "import_draft_store", None)
    if store is None:
        store = ImportDraftStore(settings.file_import_draft_ttl_seconds)
        request.app.state.import_draft_store = store
    return store


def get_document_analyzer(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VertexDocumentAnalyzer:
    """Construct the real Vertex-backed analyzer; tests inject a synthetic implementation."""
    return VertexDocumentAnalyzer(settings)


def get_narrative_adaptation_service(
    settings: Annotated[Settings, Depends(get_settings)],
    analyzer: Annotated[VertexDocumentAnalyzer, Depends(get_document_analyzer)],
) -> NarrativeAdaptationService:
    """Construct the real Vertex-backed narrative adaptation service; tests inject a synthetic implementation."""
    config = NarrativeAdaptationConfig(
        text_reader_source_unit_max_characters=settings.gemini_text_reader_source_unit_max_characters,
        text_reader_source_unit_overlap_characters=settings.gemini_text_reader_source_unit_overlap_characters,
        pdf_classifier_page_window=settings.gemini_pdf_classifier_page_window,
        pdf_reader_page_window=settings.gemini_pdf_reader_page_window,
        pdf_reader_page_overlap=settings.gemini_pdf_reader_page_overlap,
        reader_refinement_max_attempts=settings.gemini_reader_refinement_max_attempts,
        scene_regeneration_max_attempts=settings.gemini_scene_regeneration_max_attempts,
        classification_max_output_tokens=settings.gemini_classification_max_output_tokens,
        reader_max_output_tokens=settings.gemini_reader_max_output_tokens,
        planning_max_output_tokens=settings.gemini_planning_max_output_tokens,
        scene_writing_max_output_tokens=settings.gemini_scene_writing_max_output_tokens,
        verification_max_output_tokens=settings.gemini_verification_max_output_tokens,
    )
    return NarrativeAdaptationService(analyzer, config)


def get_current_user(
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
) -> CurrentUser:
    """Return the single local user, ensuring existence in DB."""
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)
    return CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)


def get_voice_design_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceDesignClient:
    """Provide the VoiceDesignClient instance."""
    from narravant.services.voice_design import create_voice_design_client

    return create_voice_design_client(settings)


def get_tts_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TtsClient:
    """Provide the TtsClient instance."""
    from narravant.services.tts import create_tts_client

    return create_tts_client(settings)
