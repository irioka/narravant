"""Documents API endpoints for NARRAVANT."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError

from narravant.api.authorization import (
    authorize_document_read,
    authorize_document_write,
    document_not_found,
)
from narravant.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_document_analyzer,
    get_document_repo,
    get_import_draft_store,
    get_narrative_adaptation_service,
    get_settings,
    get_storage_client,
    get_task_manager,
)
from narravant.api.errors import ApiError, ApiErrorCode
from narravant.api.schemas import (
    DocumentCapabilitiesSchema,
    DocumentDetailResponse,
    DocumentItem,
    DocumentListResponse,
    DocumentUpdateRequest,
    DocumentVersionItem,
    DocumentVersionListResponse,
    EmotionArcSchema,
    ImportDraftSaveRequest,
    ImportSaveResponse,
    NarratorSchema,
    SceneSchema,
    TaskAcceptedResponse,
    VoiceAssignmentSchema,
)
from narravant.api.sorting import parse_document_sorts
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.core.fountain import FountainParser
from narravant.core.settings import Settings
from narravant.core.valence_vector import ValenceVectorizer, default_valence_vectorizer
from narravant.db.database import DocumentRepository, OptimisticLockError
from narravant.services.ingestion import (
    DocumentAnalyzer,
    ImportDraftExpiredError,
    ImportDraftStore,
    ImportService,
    ImportValidationError,
    NarrativeAdapter,
    validate_import,
)
from narravant.services.tasks import TaskManager
from narravant.storage.gcs import (
    GcsConflictError,
    ScriptStorageClient,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
MEBIBYTE_BYTES = 1024 * 1024
_RETIRED_IMPORT_SEGMENT = "upload"


def _import_service(
    repo: DocumentRepository,
    storage: ScriptStorageClient,
    manager: TaskManager,
    drafts: ImportDraftStore,
    analyzer: DocumentAnalyzer,
    adapter: NarrativeAdapter,
    settings: Settings,
) -> ImportService:
    return ImportService(
        repo,
        storage,
        manager,
        drafts,
        analyzer,
        adapter,
        processing_timeout_seconds=getattr(
            settings,
            "gemini_processing_timeout_seconds",
            getattr(settings, "vertex_ai_processing_timeout_seconds", 1800),
        ),
        max_size_bytes=settings.file_import_max_size_mb * MEBIBYTE_BYTES,
        pdf_max_pages=settings.file_import_pdf_max_pages,
        fdx_max_depth=settings.file_import_fdx_max_depth,
        txt_minimum_confidence=settings.file_import_txt_minimum_confidence,
        emotion_arc_max_points=settings.emotion_arc_max_points,
    )


def _document_capabilities(doc_row: dict[str, Any], user: CurrentUser | None) -> DocumentCapabilitiesSchema:
    """Expose full owner actions to the local user with sharing disabled."""
    return DocumentCapabilitiesSchema(
        can_edit=True,
        can_share=False,
        can_delete=True,
    )


@router.api_route(
    f"/{_RETIRED_IMPORT_SEGMENT}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
@router.api_route(
    f"/{{doc_id}}/{_RETIRED_IMPORT_SEGMENT}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def reject_retired_import_route(doc_id: str | None = None) -> None:
    """Reserve retired paths so dynamic document routes cannot turn them into 401 or 405."""
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.post("/import", response_model=TaskAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def import_document(
    file: Annotated[
        UploadFile,
        File(
            description=(
                ".fountain/.txt/.pdf/.fdx/.json script. Text input uses strict charset detection; "
                "PDF input, including image-only PDF, is sent to Gemini as application/pdf. "
                "Input bytes are not persisted."
            )
        ),
    ],
    background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    manager: Annotated[TaskManager, Depends(get_task_manager)],
    drafts: Annotated[ImportDraftStore, Depends(get_import_draft_store)],
    analyzer: Annotated[DocumentAnalyzer, Depends(get_document_analyzer)],
    adapter: Annotated[NarrativeAdapter, Depends(get_narrative_adaptation_service)],
) -> TaskAcceptedResponse:
    """Start an import that returns an ephemeral draft over SSE after the 202 response."""
    max_size_bytes = settings.file_import_max_size_mb * MEBIBYTE_BYTES
    content = await file.read(max_size_bytes + 1)
    try:
        imported = validate_import(
            file.filename or "",
            content,
            max_size_bytes,
            pdf_max_pages=settings.file_import_pdf_max_pages,
            fdx_max_depth=settings.file_import_fdx_max_depth,
            txt_minimum_confidence=settings.file_import_txt_minimum_confidence,
        )
    except ImportValidationError as exc:
        raise ApiError(
            exc.status_code,
            ApiErrorCode(exc.code),
            str(exc),
        ) from exc
    service = _import_service(repo, storage, manager, drafts, analyzer, adapter, settings)
    task_id = service.start(user.user_id, imported)
    background_tasks.add_task(service.run, task_id, content)
    return TaskAcceptedResponse(task_id=task_id, task_type="document_import")


@router.post("/import/{task_id}/save", response_model=ImportSaveResponse)
async def save_import_draft(
    task_id: str,
    payload: ImportDraftSaveRequest,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    manager: Annotated[TaskManager, Depends(get_task_manager)],
    drafts: Annotated[ImportDraftStore, Depends(get_import_draft_store)],
    analyzer: Annotated[DocumentAnalyzer, Depends(get_document_analyzer)],
    adapter: Annotated[NarrativeAdapter, Depends(get_narrative_adaptation_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ImportSaveResponse:
    """Atomically publish the current client-side Import draft selected by Save."""
    service = _import_service(repo, storage, manager, drafts, analyzer, adapter, settings)
    try:
        document_id, version_id, expected_version = service.save(task_id, user.user_id, payload)
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict: Document was modified by another request (expected version {exc.expected_version})",
        ) from exc
    except GcsConflictError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.CONFLICT,
            "保存が競合しました。再読み込みして再試行してください。",
        ) from exc
    except ImportDraftExpiredError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.PROCESS_RESTARTED,
            "Import draftの有効期限が切れました。Importを再実行してください。",
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import draft not found") from exc
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "編集中のImport draftと分析結果の整合性を確認してください。",
        ) from exc
    return ImportSaveResponse(
        document_id=document_id,
        version_id=version_id,
        expected_version=expected_version,
    )


@router.delete("/import/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_import_draft(
    task_id: str,
    drafts: Annotated[ImportDraftStore, Depends(get_import_draft_store)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> None:
    """Discard only the process-local draft; no saved content is touched."""
    draft = drafts.get(task_id)
    if draft is None or draft.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import draft not found")
    drafts.discard(task_id)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    query: str | None = Query(None, description="Search term for title"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: Annotated[
        list[str] | None,
        Query(description="Repeated field:direction server-side sort terms"),
    ] = None,
    similar_to_arc_id: str | None = Query(None, description="Document ID to find similar narrative arcs"),
    arc_pattern_id: str | None = Query(
        None,
        description=(
            "Configured Valence pattern ID to find similar narrative arcs; mutually exclusive with similar_to_arc_id"
        ),
    ),
) -> DocumentListResponse:
    """List screenplays with optional filtering and narrative arc similarity matching."""
    try:
        sorts = parse_document_sorts(sort or [])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if similar_to_arc_id and arc_pattern_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="similar_to_arc_id and arc_pattern_id are mutually exclusive",
        )

    if sorts and any(item.field == "arc_distance" for item in sorts) and not (similar_to_arc_id or arc_pattern_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="arc_distance sorting requires similar_to_arc_id or arc_pattern_id",
        )

    similar_vector: list[float] | None = None

    if similar_to_arc_id:
        target_doc = authorize_document_read(repo, similar_to_arc_id, user).document
        try:
            data, _ = storage.read_structured_script(similar_to_arc_id, target_doc["current_version_id"])
        except FileNotFoundError as exc:
            raise ApiError(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                ApiErrorCode.DOCUMENT_CONTENT_MISSING,
                "類似検索対象の文書本文の正本が見つかりません。管理者に問い合わせてください。",
                {
                    "document_id": similar_to_arc_id,
                    "version_id": target_doc["current_version_id"],
                },
            ) from exc

        vec = data.get("emotion_arc", {}).get("valence_vector")
        if not isinstance(vec, list) or len(vec) != 10:
            valence = data.get("emotion_arc", {}).get("valence")
            if isinstance(valence, list) and valence:
                vec = default_valence_vectorizer.vectorize(valence)
            else:
                vec = None
        if not isinstance(vec, list) or len(vec) != 10:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Document {similar_to_arc_id} has no usable emotion arc feature vector",
            )
        similar_vector = vec
    elif arc_pattern_id:
        pattern = next(
            (item for item in settings.valence_patterns if item.pattern_id == arc_pattern_id),
            None,
        )
        if pattern is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unsupported arc_pattern_id: {arc_pattern_id}",
            )
        profile = getattr(settings, "valence_vectorization", default_valence_vectorizer.profile)
        similar_vector = ValenceVectorizer(profile).vectorize(pattern.control_values)

    max_arc_distance = getattr(settings, "valence_max_arc_distance", None) if similar_vector is not None else None

    rows, total = repo.list_documents(
        query=query,
        limit=limit,
        offset=offset,
        similar_to_vector=similar_vector,
        max_arc_distance=max_arc_distance,
        sorts=sorts,
        viewer_user_id=user.user_id,
        viewer_email=user.email,
    )

    items = [DocumentItem(**r) for r in rows]
    return DocumentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{doc_id}/versions", response_model=DocumentVersionListResponse)
async def list_document_versions(
    doc_id: str,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> DocumentVersionListResponse:
    authorize_document_read(repo, doc_id, user)
    history = repo.list_document_versions(doc_id)
    if history is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {doc_id} not found")
        raise document_not_found()
    current_version_id, rows = history
    return DocumentVersionListResponse(
        document_id=doc_id,
        current_version_id=current_version_id,
        items=[
            DocumentVersionItem(
                **row,
                is_current=row["version_id"] == current_version_id,
            )
            for row in rows
        ],
    )


@router.get("/{doc_id}/versions/{version_id}", response_model=DocumentDetailResponse)
async def get_document_version(
    doc_id: str,
    version_id: int,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> DocumentDetailResponse:
    authorize_document_read(repo, doc_id, user)
    version = repo.get_document_version(doc_id, version_id)
    if version is None:
        raise document_not_found()
    return await get_document_detail(doc_id, repo, storage, requested_version_id=version_id, user=user)


@router.get("/{doc_id}", response_model=DocumentDetailResponse)
async def get_document_detail(
    doc_id: str,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    requested_version_id: int | None = None,
) -> DocumentDetailResponse:
    """Fetch complete document details, scenes, and emotion arc from GCS."""
    doc_row = authorize_document_read(repo, doc_id, user).document

    version_id = requested_version_id if requested_version_id is not None else doc_row["current_version_id"]
    version_metadata = repo.get_document_version(doc_id, version_id)
    if version_metadata is None:
        raise document_not_found()
    try:
        data, metadata = storage.read_structured_script(doc_id, version_id)
    except FileNotFoundError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ApiErrorCode.DOCUMENT_CONTENT_MISSING,
            "文書本文の正本が見つかりません。管理者に問い合わせてください。",
            {"document_id": doc_id, "version_id": version_id},
        ) from exc

    try:
        scenes_schema = [SceneSchema(**scene) for scene in data["scenes"]]
        arc_schema = EmotionArcSchema(**data["emotion_arc"])
        narrator_data = data.get("narrator")
        narrator = NarratorSchema(**narrator_data) if isinstance(narrator_data, dict) else NarratorSchema()
        voice_assignments_data = data.get("voice_assignments")
        voice_assignments = (
            [VoiceAssignmentSchema(**va) for va in voice_assignments_data]
            if isinstance(voice_assignments_data, list)
            else []
        )
        return DocumentDetailResponse(
            document_id=doc_row["document_id"],
            owner_user_id=doc_row["owner_user_id"],
            title=version_metadata["title"],
            current_version_id=doc_row["current_version_id"],
            version_id=version_id,
            expected_version=doc_row["version_id"],
            is_saved=bool(version_metadata["is_saved"]),
            created_at=doc_row["created_at"],
            updated_at=version_metadata["created_at"],
            owner_email=doc_row["owner_email"],
            shared_count=doc_row["shared_count"],
            gcs_uri=version_metadata["gcs_uri"],
            generation=metadata.generation,
            source_fountain=data["source_fountain"],
            capabilities=_document_capabilities(doc_row, user),
            metadata=data["metadata"],
            scenes=scenes_schema,
            analysis=data["analysis"],
            emotion_arc=arc_schema,
            narrator=narrator,
            voice_assignments=voice_assignments,
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ApiErrorCode.DOCUMENT_CONTENT_MISSING,
            "文書本文の正本が不完全です。管理者に問い合わせてください。",
            {"document_id": doc_id, "version_id": version_id},
        ) from exc


@router.put("/{doc_id}", response_model=DocumentDetailResponse)
async def update_document(
    doc_id: str,
    payload: DocumentUpdateRequest,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    storage: Annotated[ScriptStorageClient, Depends(get_storage_client)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentDetailResponse:
    """Update document metadata and/or script body with optimistic lock validation."""
    current_doc = authorize_document_write(repo, doc_id, user).document

    base_version_id = payload.base_version_id or int(current_doc["current_version_id"])
    base_metadata = repo.get_document_version(doc_id, base_version_id)
    if base_metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document version {base_version_id} not found",
        )
        raise document_not_found()
    try:
        base_payload, storage_metadata = storage.read_structured_script(doc_id, base_version_id)
    except FileNotFoundError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ApiErrorCode.DOCUMENT_CONTENT_MISSING,
            "文書本文の正本が見つかりません。管理者に問い合わせてください。",
            {"document_id": doc_id, "version_id": base_version_id},
        ) from exc

    structured_json = deepcopy(base_payload)
    if payload.fountain_text is not None:
        parsed = FountainParser.parse(payload.fountain_text)
        structured_json["source_fountain"] = parsed.source_fountain
        structured_json["scenes"] = [scene.to_dict() for scene in parsed.scenes]
        structured_json["source"]["sha256"] = sha256(parsed.source_fountain.encode("utf-8")).hexdigest()
    if payload.metadata is not None:
        structured_json["metadata"] = {
            **structured_json["metadata"],
            **payload.metadata,
        }
    if payload.analysis is not None:
        structured_json["analysis"] = payload.analysis.model_dump()
    if payload.emotion_arc is not None:
        structured_json["emotion_arc"] = payload.emotion_arc.model_dump()
    if payload.narrator is not None:
        structured_json["narrator"] = payload.narrator.model_dump()
    if payload.voice_assignments is not None:
        structured_json["voice_assignments"] = [assignment.model_dump() for assignment in payload.voice_assignments]
    if payload.title is not None:
        structured_json["metadata"]["title"] = payload.title.strip()
    title = str(structured_json["metadata"].get("title", "")).strip()
    if not title:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "titleがありません。",
        )
    if payload.emotion_arc is not None and structured_json.get("analysis", {}).get("status") != "completed":
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "未分析の文書にEmotional Arcを保存できません。",
        )
    _validate_preserved_analysis(structured_json, max_points=settings.emotion_arc_max_points)

    unchanged = structured_json == base_payload
    try:
        if unchanged:
            if bool(base_metadata["is_saved"]):
                repo.activate_existing_version(doc_id, payload.expected_version, base_version_id)
            else:
                _finalize_draft_with_search(
                    repo,
                    storage,
                    doc_id,
                    payload.expected_version,
                    base_version_id,
                    title,
                    storage_metadata.uri,
                    storage_metadata.generation,
                    storage_metadata.sha256,
                    structured_json["emotion_arc"]["valence_vector"],
                )
        elif not bool(base_metadata["is_saved"]):
            stored = storage.write_structured_script(
                doc_id,
                base_version_id,
                structured_json,
                if_generation_match=storage_metadata.generation,
            )
            _finalize_draft_with_search(
                repo,
                storage,
                doc_id,
                payload.expected_version,
                base_version_id,
                title,
                stored.uri,
                stored.generation,
                stored.sha256,
                structured_json["emotion_arc"]["valence_vector"],
            )
        elif _only_title_changed(base_payload, structured_json):
            # Title-only edit: rename in SQLite (documents + current version row)
            # WITHOUT creating a new content version or rewriting stored content
            # (renaming must not bump the content version). The detail response
            # reads the title from the current document_versions row.
            repo.rename_document(
                document_id=doc_id,
                expected_version=payload.expected_version,
                title=title,
            )
        else:
            next_ver = repo.next_content_version_id(doc_id)
            structured_json["version_id"] = next_ver
            stored = storage.write_structured_script(doc_id, next_ver, structured_json, if_generation_match=0)
            try:
                search_uri = storage.search_text_uri(doc_id)
                search_generation = storage.stat_uri(search_uri).generation
                storage.write_search_text(
                    doc_id,
                    structured_json["source_fountain"],
                    if_generation_match=search_generation,
                )
                repo.update_document(
                    document_id=doc_id,
                    expected_version=payload.expected_version,
                    title=title,
                    new_gcs_uri=stored.uri,
                    new_gcs_generation=stored.generation,
                    new_payload_sha256=stored.sha256,
                    new_valence_vector=structured_json["emotion_arc"]["valence_vector"],
                    new_content_version_id=next_ver,
                )
            except Exception:
                try:
                    storage.delete_uri(stored.uri, stored.generation)
                except (FileNotFoundError, GcsConflictError):
                    pass
                raise
    except OptimisticLockError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict: Document was modified by another request (expected version {e.expected_version})",
        ) from e
    except GcsConflictError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ApiErrorCode.CONFLICT,
            "文書の保存が競合しました。再読み込みして保存してください。",
            {"document_id": doc_id},
        ) from exc

    # Return latest detail
    return await get_document_detail(doc_id=doc_id, repo=repo, storage=storage, user=user)


def _only_title_changed(base_payload: dict[str, Any], updated: dict[str, Any]) -> bool:
    """True if `updated` differs from `base_payload` only in metadata.title.

    Used so a rename does not create a new content version (renaming should be
    cheap and must not bump the content version). Compares deep copies with the
    title normalized out.
    """
    if base_payload == updated:
        return False
    base_copy = deepcopy(base_payload)
    updated_copy = deepcopy(updated)
    base_meta = base_copy.get("metadata")
    updated_meta = updated_copy.get("metadata")
    if not isinstance(base_meta, dict) or not isinstance(updated_meta, dict):
        return False
    base_meta.pop("title", None)
    updated_meta.pop("title", None)
    # `valence_vector` is a local derived value and is regenerated from the
    # canonical Valence curve on every content save.
    for payload in (base_copy, updated_copy):
        emotion_arc = payload.get("emotion_arc")
        if isinstance(emotion_arc, dict):
            emotion_arc.pop("valence_vector", None)
    return base_copy == updated_copy


def _finalize_draft_with_search(
    repo: DocumentRepository,
    storage: ScriptStorageClient,
    document_id: str,
    expected_version: int,
    draft_version_id: int,
    title: str,
    gcs_uri: str,
    gcs_generation: int,
    payload_sha256: str,
    valence_vector: list[float],
) -> None:
    """Publish one draft only after its search text and SQLite current pointer can both be updated."""
    search_uri = storage.search_text_uri(document_id)
    old_search_text = storage.read_search_text(document_id)
    if old_search_text is None:
        raise FileNotFoundError(f"Canonical search text not found: {search_uri}")
    old_search_generation = storage.stat_uri(search_uri).generation
    draft_payload, _ = storage.read_structured_script(document_id, draft_version_id)
    search = storage.write_search_text(
        document_id,
        draft_payload["source_fountain"],
        if_generation_match=old_search_generation,
    )
    try:
        repo.finalize_draft_version(
            document_id,
            expected_version,
            draft_version_id,
            title,
            gcs_uri,
            gcs_generation,
            payload_sha256,
            valence_vector,
        )
    except Exception:
        try:
            storage.write_search_text(document_id, old_search_text, if_generation_match=search.generation)
        except GcsConflictError as cleanup_exc:
            raise ApiError(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                ApiErrorCode.INTERNAL_ERROR,
                "保存失敗後の検索テキスト復元に失敗しました。管理者に問い合わせてください。",
                {"document_id": document_id},
            ) from cleanup_exc
        raise


def _validate_preserved_analysis(payload: dict[str, Any], *, max_points: int) -> None:
    """Reject a body save that would silently detach completed analysis from scenes."""
    if payload.get("analysis", {}).get("status") != "completed":
        return
    scene_count = len(payload.get("scenes", []))
    arc = payload.get("emotion_arc", {})
    valence = arc.get("valence")
    tension = arc.get("tension")
    characters = arc.get("characters")
    expected_arc_length = min(scene_count, max_points)
    if not isinstance(valence, list) or len(valence) != expected_arc_length:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "シーン数が変わる本文は、先にValenceを再分析してください。",
        )
    if not isinstance(tension, list) or len(tension) != expected_arc_length:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "tensionとシーン数が一致しません。",
        )
    if not isinstance(characters, dict) or any(
        not isinstance(values, list) or len(values) != expected_arc_length for values in characters.values()
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "キャラクター別Valenceとシーン数が一致しません。",
        )
    profile_names = {item.get("name") for item in payload["analysis"].get("characters", []) if item.get("name")}
    if set(characters) != profile_names:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "キャラクタープロファイルとValenceが一致しません。",
        )
    if arc.get("scene_mapping") != scene_mapping_payload(scene_count, max_points):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "シーン数が変わる本文は、先にValenceを再分析してください。",
        )
    if any(not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 7 for value in valence):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "Valenceには1〜7の整数を指定してください。",
        )
    if any(not isinstance(value, int) or isinstance(value, bool) or not -3 <= value <= 3 for value in tension):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "Tensionには-3〜+3の整数を指定してください。",
        )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 7
        for arc in characters.values()
        for value in arc
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ApiErrorCode.VALIDATION_ERROR,
            "キャラクター別Valenceには0（非登場）〜7の整数を指定してください。",
        )
    payload["emotion_arc"]["valence_vector"] = default_valence_vectorizer.vectorize([float(value) for value in valence])


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> None:
    """Soft-delete a document."""
    authorize_document_write(repo, doc_id, user)
    success = repo.soft_delete_document(doc_id)
    if not success:
        raise document_not_found()
