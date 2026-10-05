"""Emotional Arc pattern similarity endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from narravant.api.authorization import authorize_document_read, authorize_document_write
from narravant.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_document_analyzer,
    get_document_repo,
    get_import_draft_store,
    get_settings,
    get_storage_client,
    get_task_manager,
)
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.api.schemas import (
    ImportDraftReanalyzeRequest,
    ReanalyzeEmotionArcRequest,
    TaskAcceptedResponse,
    ValencePatternItem,
    ValencePatternListResponse,
    ValenceSimilarityItem,
    ValenceSimilarityResponse,
)
from narravant.core.settings import Settings
from narravant.core.valence_pattern import valence_pattern_similarities
from narravant.core.valence_vector import ValenceVectorizer, default_valence_vectorizer
from narravant.db.database import DocumentRepository
from narravant.services.ingestion import DocumentAnalyzer, ImportDraftStore
from narravant.services.reanalysis import ImportDraftReanalysisService, ReanalysisService
from narravant.services.tasks import TaskManager
from narravant.storage.gcs import ScriptStorageClient

router = APIRouter(prefix="/api/v1/documents", tags=["emotion-arc"])
catalog_router = APIRouter(prefix="/api/v1/emotion-arc", tags=["emotion-arc"])


@catalog_router.get("/patterns", response_model=ValencePatternListResponse)
def get_valence_patterns(
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[CurrentUser, Depends(get_current_user)],
) -> ValencePatternListResponse:
    """Return labels from config.yaml; curve vectors remain server-side search inputs."""
    return ValencePatternListResponse(
        items=[
            ValencePatternItem(
                pattern_id=pattern.pattern_id,
                name=pattern.name,
                description=pattern.description,
            )
            for pattern in settings.valence_patterns
        ]
    )


@router.get("/{doc_id}/emotion-arc/similarities", response_model=ValenceSimilarityResponse)
async def get_emotion_arc_similarities(
    doc_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ValenceSimilarityResponse:
    """Compare the selected document's completed valence arc to all configured Valence patterns."""
    document = authorize_document_read(repo, doc_id, user).document
    version_id = int(document["current_version_id"])
    try:
        payload, _ = storage.read_structured_script(doc_id, version_id)
    except FileNotFoundError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ApiErrorCode.DOCUMENT_CONTENT_MISSING,
            "文書本文の正本が見つかりません。管理者に問い合わせてください。",
            {"document_id": doc_id, "version_id": version_id},
        ) from exc
    valence = payload.get("emotion_arc", {}).get("valence")
    if payload.get("analysis", {}).get("status") != "completed" or not isinstance(valence, list) or not valence:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.CONFLICT,
            "選択した文書には利用可能なValenceがありません。",
            {"document_id": doc_id, "version_id": version_id},
        )
    items = valence_pattern_similarities(
        valence,
        settings.valence_patterns,
        ValenceVectorizer(getattr(settings, "valence_vectorization", default_valence_vectorizer.profile)),
    )
    return ValenceSimilarityResponse(
        document_id=doc_id,
        version_id=version_id,
        items=[
            ValenceSimilarityItem(
                pattern_id=item.pattern_id,
                name=item.name,
                description=item.description,
                percentage=item.percentage,
            )
            for item in items
        ],
    )


@router.post(
    "/{doc_id}/emotion-arc/reanalyze", response_model=TaskAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def reanalyze_emotion_arc(
    doc_id: str,
    background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    tasks: Annotated[TaskManager, Depends(get_task_manager)],
    analyzer: Annotated[DocumentAnalyzer, Depends(get_document_analyzer)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: ReanalyzeEmotionArcRequest | None = None,
) -> TaskAcceptedResponse:
    """Queue an owner-only reanalysis that returns an unsaved emotion-arc draft by SSE."""
    document = authorize_document_write(repo, doc_id, user).document
    version_id = int(document["current_version_id"])
    try:
        source, _ = storage.read_structured_script(doc_id, version_id)
    except FileNotFoundError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ApiErrorCode.DOCUMENT_CONTENT_MISSING,
            "文書本文の正本が見つかりません。管理者に問い合わせてください。",
            {"document_id": doc_id, "version_id": version_id},
        ) from exc
    if source["analysis"]["status"] != "completed":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.CONFLICT,
            "分析が完了していない文書は再分析できません。",
            {"document_id": doc_id, "version_id": version_id},
        )
    service = ReanalysisService(
        repo,
        storage,
        tasks,
        analyzer,
        emotion_arc_max_points=settings.emotion_arc_max_points,
    )
    task_id = service.start(user.user_id, document)
    background_tasks.add_task(
        service.run,
        task_id,
        source_fountain=request.source_fountain if request else None,
    )
    return TaskAcceptedResponse(task_id=task_id, task_type="emotion_arc_reanalysis")


@router.post(
    "/import/{import_task_id}/emotion-arc/reanalyze",
    response_model=TaskAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reanalyze_import_draft_emotion_arc(
    import_task_id: str,
    background_tasks: BackgroundTasks,
    request: ImportDraftReanalyzeRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    drafts: Annotated[ImportDraftStore, Depends(get_import_draft_store)],
    tasks: Annotated[TaskManager, Depends(get_task_manager)],
    analyzer: Annotated[DocumentAnalyzer, Depends(get_document_analyzer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskAcceptedResponse:
    """Queue an owner-only Emotional Arc reanalysis for an unsaved Import draft."""
    draft = drafts.get(import_task_id)
    if draft is None or draft.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import draft not found")
    if draft.structured.get("analysis", {}).get("status") != "completed":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.CONFLICT,
            "分析が完了していないImport draftは再分析できません。",
            {"import_task_id": import_task_id},
        )
    service = ImportDraftReanalysisService(
        tasks,
        drafts,
        analyzer,
        emotion_arc_max_points=settings.emotion_arc_max_points,
    )
    task_id = service.start(user.user_id, draft)
    background_tasks.add_task(
        service.run,
        task_id,
        import_task_id=import_task_id,
        source_fountain=request.source_fountain,
    )
    return TaskAcceptedResponse(task_id=task_id, task_type="emotion_arc_reanalysis")
