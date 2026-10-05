"""Import contract checkpoints: no persistence before Save, then CAS-backed publication."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter

from narravant.api.dependencies import CurrentUser
from narravant.api.documents import save_import_draft
from narravant.api.emotion_arc import reanalyze_import_draft_emotion_arc
from narravant.api.schemas import ImportDraftReanalyzeRequest, ImportDraftSaveRequest
from narravant.db.database import DatabaseManager, DocumentRepository, TaskRepository
from narravant.main import app
from narravant.services.ingestion import (
    ImportDraftStore,
    ImportService,
    ImportValidationError,
    draft_response,
    validate_import,
)
from narravant.services.narrative_adaptation import (
    NarrativeAdaptationError,
    SourceKind,
)
from narravant.services.reanalysis import ImportDraftReanalysisService
from narravant.services.tasks import EphemeralTaskEvents, TaskManager
from narravant.services.vertex_analysis import CanonicalAnalysis
from narravant.storage.gcs import InMemoryScriptStorageClient
from narravant.storage.local import LocalScriptStorageClient


def _native_bytes() -> bytes:
    return json.dumps(
        {
            "format": "narravant-native",
            "format_version": 1,
            "exported_at": "2026-09-12T12:34:56Z",
            "document": {
                "title": "Native",
                "source_fountain": "Title: Native\n\nINT. ROOM - DAY #1#\n\nAction.\n",
                "metadata": {
                    "title": "Native",
                    "logline": "Logline",
                    "synopsis": "Synopsis",
                    "theme_setting": "Theme",
                },
                "analysis": {
                    "status": "completed",
                    "turning_points": [
                        {
                            "tp_number": number,
                            "availability": "identified",
                            "scene_number": 1,
                            "change": f"Change {number}",
                            "involved_characters": [
                                {
                                    "name": "A",
                                    "goal": "g",
                                    "conflict": "c",
                                    "choice": "q",
                                    "action": "a",
                                    "change": "d",
                                }
                            ],
                            "reason": None,
                        }
                        for number in range(1, 6)
                    ],
                    "characters": [
                        {
                            "name": "A",
                            "external_goal": "Goal",
                            "internal_need": "Need",
                            "fear_or_cost": "Cost",
                            "obstacle": "Obstacle",
                            "choice": "Choice",
                            "agency": "Agency",
                            "goal_to_outcome": "Outcome",
                            "related_turning_points": [1],
                        }
                    ],
                },
                "emotion_arc": {
                    "valence": [4],
                    "tension": [0],
                    "characters": {"A": [4]},
                },
            },
        }
    ).encode()


class NoGeminiAnalyzer:
    def normalize_screenplay_text(self, *_args, **_kwargs):  # pragma: no cover - assertion carries the contract
        raise AssertionError("Native JSON must not call Gemini")

    def normalize_screenplay_pdf(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def convert_text_to_fountain(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def convert_pdf_to_fountain(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def analyze(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")


class NoGeminiAdapter:
    def classify_text(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def classify_pdf(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def adapt_text(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")

    def adapt_pdf(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Native JSON must not call Gemini")


class TransientPdfAdapter:
    def classify_pdf(self, *_args, **_kwargs):
        raise RuntimeError("upstream-only diagnostic must not reach the application log")


def _service() -> tuple[ImportService, DocumentRepository, InMemoryScriptStorageClient, TaskManager]:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    tasks = TaskManager(TaskRepository(db), ephemeral_events=EphemeralTaskEvents())
    service = ImportService(
        repository,
        storage,
        tasks,
        ImportDraftStore(3600),
        NoGeminiAnalyzer(),
        NoGeminiAdapter(),
        processing_timeout_seconds=60,
        max_size_bytes=1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
    )
    return service, repository, storage, tasks


def test_native_import_keeps_every_content_store_empty_until_explicit_save() -> None:
    service, repository, storage, tasks = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
    )
    task_id = service.start("owner-1", imported)

    service.run(task_id, content)

    task = tasks.repository.get(task_id)
    assert task is not None and task["status"] == "completed"
    assert repository.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}  # type: ignore[attr-defined]
    persisted_payloads = [event.payload for event in tasks.events_after(task_id, 0) if event.event_type != "completed"]
    assert all("source_fountain" not in json.dumps(payload) for payload in persisted_payloads)
    completed = tasks.events_after(task_id, 0)[-1]
    assert completed.event_type == "completed"
    assert completed.payload["import_draft"]["document_id"] == task["document_id"]

    document_id, content_version, lock_version = service.save(task_id, "owner-1")

    assert content_version == 1 and lock_version == 1
    assert repository.get_document(document_id) is not None
    persisted, _ = storage.read_structured_script(document_id, 1)
    assert persisted["metadata"]["title"] == "Native"
    assert storage.read_search_text(document_id) == persisted["source_fountain"]


def test_import_after_a_saved_document_always_creates_a_distinct_unsaved_draft() -> None:
    """Import never uses the selected document as a replacement target."""
    service, repository, storage, _ = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json", content, 1024 * 1024, pdf_max_pages=100, fdx_max_depth=32, txt_minimum_confidence=0.70
    )
    original_task_id = service.start("owner-1", imported)
    service.run(original_task_id, content)
    original_document_id, _, _ = service.save(original_task_id, "owner-1")

    second_task_id = service.start("owner-1", imported)
    service.run(second_task_id, content)
    second_draft = service.drafts.get(second_task_id)

    assert second_draft is not None
    assert second_draft.document_id != original_document_id
    assert second_draft.expected_version is None
    assert repository.list_documents(limit=10, offset=0)[1] == 1
    original, _ = storage.read_structured_script(original_document_id, 1)
    assert original["metadata"]["title"] == "Native"


@pytest.mark.asyncio
async def test_import_draft_reanalysis_api_is_owner_only_and_does_not_persist_editor_text() -> None:
    service, _, _, tasks = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json", content, 1024 * 1024, pdf_max_pages=100, fdx_max_depth=32, txt_minimum_confidence=0.70
    )
    import_task_id = service.start("owner-1", imported)
    service.run(import_task_id, content)
    source_fountain = "Title: Unsaved Native Edit\n\nINT. EDITED ROOM - NIGHT #1#\n\n@BOB\nSynthetic line."
    request = ImportDraftReanalyzeRequest(source_fountain=source_fountain)
    background = BackgroundTasks()

    accepted = await reanalyze_import_draft_emotion_arc(
        import_task_id=import_task_id,
        background_tasks=background,
        request=request,
        user=CurrentUser("owner-1", "owner@example.test"),
        drafts=service.drafts,
        tasks=tasks,
        analyzer=NoGeminiAnalyzer(),
        settings=SimpleNamespace(emotion_arc_max_points=36),
    )

    queued = tasks.repository.get(accepted.task_id)
    assert accepted.task_type == "emotion_arc_reanalysis"
    assert queued is not None and queued["status"] == "queued"
    assert source_fountain not in queued["payload_json"]
    assert len(background.tasks) == 1
    assert background.tasks[0].kwargs == {
        "import_task_id": import_task_id,
        "source_fountain": source_fountain,
    }

    with pytest.raises(HTTPException) as caught:
        await reanalyze_import_draft_emotion_arc(
            import_task_id=import_task_id,
            background_tasks=BackgroundTasks(),
            request=request,
            user=CurrentUser("other-user", "other@example.test"),
            drafts=service.drafts,
            tasks=tasks,
            analyzer=NoGeminiAnalyzer(),
            settings=SimpleNamespace(emotion_arc_max_points=36),
        )
    assert caught.value.status_code == 404


def test_import_draft_reanalysis_uses_editor_fountain_without_publishing_a_document() -> None:
    from narravant.core.emotion_arc_resolution import scene_mapping_payload
    from narravant.services.vertex_analysis import CanonicalAnalysis

    import_service, repository, storage, tasks = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json", content, 1024 * 1024, pdf_max_pages=100, fdx_max_depth=32, txt_minimum_confidence=0.70
    )
    import_task_id = import_service.start("owner-1", imported)
    import_service.run(import_task_id, content)
    draft = import_service.drafts.get(import_task_id)
    assert draft is not None
    original_draft = deepcopy(draft.structured)
    source_fountain = "Title: Unsaved Native Edit\n\nINT. EDITED ROOM - NIGHT #1#\n\n@BOB\nSynthetic line."
    analyzed_sources: list[str] = []

    class Analyzer:
        def analyze(self, fountain, _on_progress, *, expected_characters=None):
            analyzed_sources.append(fountain)
            assert expected_characters is None
            return CanonicalAnalysis(
                metadata={},
                emotion_arc={
                    "valence": [6],
                    "tension": [1],
                    "characters": {"BOB": [6]},
                    "scene_mapping": scene_mapping_payload(1, 36),
                },
                characters=[{"name": "BOB"}],
                turning_points=deepcopy(draft.structured["analysis"]["turning_points"]),
            )

    reanalysis = ImportDraftReanalysisService(
        tasks,
        import_service.drafts,
        Analyzer(),
        emotion_arc_max_points=36,
    )
    reanalysis_task_id = reanalysis.start("owner-1", draft)

    reanalysis.run(
        reanalysis_task_id,
        import_task_id=import_task_id,
        source_fountain=source_fountain,
    )

    task = tasks.repository.get(reanalysis_task_id)
    completed = tasks.events_after(reanalysis_task_id, 0)[-1]
    assert task is not None and task["status"] == "completed"
    assert source_fountain not in task["payload_json"]
    assert analyzed_sources == [source_fountain]
    assert import_service.drafts.get(import_task_id).structured == original_draft  # type: ignore[union-attr]
    assert repository.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}  # type: ignore[attr-defined]
    assert completed.payload["reanalysis"]["emotion_arc"]["characters"] == {"BOB": [6]}
    assert source_fountain not in json.dumps(completed.payload)


def test_import_save_publishes_the_current_client_edited_draft_atomically() -> None:
    """Import画面で編集した本文・分析を、最初のSaveで一度だけ確定する。"""
    service, repository, storage, _ = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)
    draft = service.drafts.get(task_id)
    assert draft is not None
    assert repository.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}  # type: ignore[attr-defined]

    metadata = deepcopy(draft.structured["metadata"])
    metadata["synopsis"] = "Edited synopsis"
    analysis = deepcopy(draft.structured["analysis"])
    analysis["turning_points"][0]["label"] = "tampered label"
    analysis["characters"][0]["external_goal"] = "Edited goal"
    emotion_arc = deepcopy(draft.structured["emotion_arc"])
    emotion_arc["valence_vector"] = [999.0] * 10
    edits = ImportDraftSaveRequest(
        title="Edited Native",
        fountain_text=str(draft.structured["source_fountain"]),
        metadata=metadata,
        analysis=analysis,
        emotion_arc=emotion_arc,
    )

    document_id, content_version, lock_version = service.save(task_id, "owner-1", edits)

    assert content_version == 1 and lock_version == 1
    persisted, _ = storage.read_structured_script(document_id, 1)
    assert persisted["metadata"]["title"] == "Edited Native"
    assert persisted["metadata"]["synopsis"] == "Edited synopsis"
    assert persisted["analysis"]["characters"][0]["external_goal"] == "Edited goal"
    assert persisted["analysis"]["turning_points"][0]["label"] == "Opportunity"
    assert persisted["emotion_arc"]["valence_vector"] != [999.0] * 10
    assert storage.read_search_text(document_id) == persisted["source_fountain"]


@pytest.mark.asyncio
async def test_import_save_api_accepts_and_publishes_the_current_client_draft() -> None:
    """Save APIはSSEで展開済みの編集ドラフトを同一のpublishで確定する。"""
    service, repository, storage, tasks = _service()
    content = _native_bytes()
    imported = validate_import(
        "native.json",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)
    draft = service.drafts.get(task_id)
    assert draft is not None
    metadata = deepcopy(draft.structured["metadata"])
    metadata["synopsis"] = "Saved through API"
    metadata["characters"] = ["Character A", "Character B"]
    payload = ImportDraftSaveRequest(
        title="API Edited Native",
        fountain_text=str(draft.structured["source_fountain"]),
        metadata=metadata,
        analysis=deepcopy(draft.structured["analysis"]),
        emotion_arc=deepcopy(draft.structured["emotion_arc"]),
    )
    settings = SimpleNamespace(
        gemini_processing_timeout_seconds=60,
        vertex_ai_processing_timeout_seconds=60,
        file_import_max_size_mb=1,
        file_import_pdf_max_pages=100,
        file_import_fdx_max_depth=32,
        file_import_txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
        sse_poll_interval_milliseconds=100,
        file_import_draft_ttl_seconds=3600,
    )
    response = await save_import_draft(
        task_id,
        payload,
        repository,
        storage,
        tasks,
        service.drafts,
        NoGeminiAnalyzer(),
        NoGeminiAdapter(),
        settings,
        CurrentUser("owner-1", "owner@example.test"),
    )

    assert response.document_id == draft.document_id
    assert repository.get_document(draft.document_id) is not None
    persisted, _ = storage.read_structured_script(draft.document_id, 1)
    assert persisted["metadata"]["title"] == "API Edited Native"
    assert persisted["metadata"]["synopsis"] == "Saved through API"


def test_import_validation_rejects_docx_and_non_utf8_fountain() -> None:
    with pytest.raises(ImportValidationError) as unsupported:
        validate_import(
            "legacy.docx",
            b"not-a-word-file",
            1024,
            pdf_max_pages=100,
            fdx_max_depth=32,
            txt_minimum_confidence=0.70,
        )
    assert unsupported.value.status_code == 422
    with pytest.raises(ImportValidationError) as invalid_encoding:
        validate_import(
            "script.fountain",
            b"\xff\xfe",
            1024,
            pdf_max_pages=100,
            fdx_max_depth=32,
            txt_minimum_confidence=0.70,
        )
    assert invalid_encoding.value.status_code == 422


def test_import_validation_accepts_txt_fdx_and_preflighted_pdf() -> None:
    txt = validate_import(
        "script.txt",
        b"INT. ROOM - DAY\n\nAction.",
        1024,
        pdf_max_pages=1,
        fdx_max_depth=8,
        txt_minimum_confidence=0.70,
    )
    assert txt.text is not None and txt.extension == ".txt"

    fdx = validate_import(
        "script.fdx",
        (
            b"<FinalDraft><Content><Paragraph Type='Scene Heading'><Text>INT. ROOM - DAY</Text></Paragraph>"
            b"<Paragraph Type='Character'><Text>ALICE</Text></Paragraph>"
            b"<Paragraph Type='Dialogue'><Text>Hello.</Text></Paragraph></Content></FinalDraft>"
        ),
        1024,
        pdf_max_pages=1,
        fdx_max_depth=8,
        txt_minimum_confidence=0.70,
    )
    assert fdx.text == "INT. ROOM - DAY\n\n@ALICE\n\nHello."

    pdf_stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_stream)
    pdf = validate_import(
        "script.pdf",
        pdf_stream.getvalue(),
        1024 * 1024,
        pdf_max_pages=1,
        fdx_max_depth=8,
        txt_minimum_confidence=0.70,
    )
    assert pdf.extension == ".pdf" and pdf.text is None


def test_transient_vertex_failure_logs_safe_summary_without_sdk_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _, tasks = _service()
    service.adapter = TransientPdfAdapter()  # type: ignore[assignment]
    monkeypatch.setattr("narravant.services.ingestion.is_transient_vertex_error", lambda _exc: True)
    pdf_stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_stream)
    content = pdf_stream.getvalue()
    imported = validate_import(
        "scanned.pdf",
        content,
        1024 * 1024,
        pdf_max_pages=1,
        fdx_max_depth=8,
        txt_minimum_confidence=0.70,
    )
    task_id = service.start("owner-1", imported)

    caplog.set_level(logging.ERROR, logger="narravant.services.ingestion")
    service.run(task_id, content)

    error = tasks.events_after(task_id, 0)[-1]
    assert error.event_type == "error"
    assert error.payload["retryable"] is True
    records = [record for record in caplog.records if record.name == "narravant.services.ingestion"]
    assert len(records) == 1
    assert records[0].exc_info is None
    assert "Traceback" not in caplog.text
    assert "upstream-only diagnostic" not in caplog.text


@pytest.mark.parametrize(
    ("filename", "content", "max_size", "expected_status"),
    [
        ("empty.fountain", b" \n\t", 1024, 422),
        ("large.fountain", b"x" * 5, 4, 413),
        ("invalid.fdx", b"<FinalDraft>", 1024, 422),
        ("invalid.pdf", b"%PDF-not-a-document", 1024, 422),
    ],
)
def test_import_validation_rejects_empty_oversize_and_malformed_files(
    filename: str, content: bytes, max_size: int, expected_status: int
) -> None:
    with pytest.raises(ImportValidationError) as caught:
        validate_import(
            filename,
            content,
            max_size,
            pdf_max_pages=1,
            fdx_max_depth=8,
            txt_minimum_confidence=0.70,
        )
    assert caught.value.status_code == expected_status


@pytest.mark.asyncio
async def test_legacy_routes_are_not_registered() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/documents/" + "upload")).status_code == 404
        assert (await client.post("/api/v1/documents/a-document/" + "upload")).status_code == 404
        assert (await client.get("/api/v1/documents/" + "upload")).status_code == 404
        assert (await client.get("/api/v1/documents/a-document/" + "upload")).status_code == 404


class FakeNarrativeAdapter:
    def __init__(
        self,
        text_kind: SourceKind = SourceKind.NARRATIVE_PROSE,
        pdf_kind: SourceKind = SourceKind.NARRATIVE_PROSE,
    ) -> None:
        self.text_kind = text_kind
        self.pdf_kind = pdf_kind
        self.calls: list[str] = []
        self.fountain = "Title: Adapted\n\nINT. ROOM - DAY #1#\n\nAdapted scene.\n"
        self.adapt_languages: list[str] = []

    def classify_text(self, text: str, on_progress=None, deadline=None) -> SourceKind:
        self.calls.append("classify_text")
        if on_progress:
            on_progress(100)
        return self.text_kind

    def classify_pdf(self, pdf_bytes: bytes, on_progress=None, deadline=None) -> SourceKind:
        self.calls.append("classify_pdf")
        if on_progress:
            on_progress(100)
        return self.pdf_kind

    def adapt_text(
        self,
        text: str,
        on_progress=None,
        ensure_not_cancelled=None,
        deadline=None,
        *,
        source_language: str = "und",
    ) -> str:
        self.calls.append("adapt_text")
        self.adapt_languages.append(source_language)
        if on_progress:
            on_progress("reading", 1)
        return self.fountain

    def adapt_pdf(
        self,
        pdf_bytes: bytes,
        on_progress=None,
        ensure_not_cancelled=None,
        deadline=None,
    ) -> str:
        self.calls.append("adapt_pdf")
        if on_progress:
            on_progress("reading", 1)
        return self.fountain


class FakeDocumentAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fountain = "Title: Normalized\n\nINT. ROOM - DAY #1#\n\nNormalized scene.\n"
        self.normalize_languages: list[str] = []
        self.analyze_languages: list[str] = []

    def normalize_screenplay_text(
        self,
        text: str,
        on_progress=None,
        deadline=None,
        *,
        source_language: str = "und",
    ) -> str:
        self.calls.append("normalize_screenplay_text")
        self.normalize_languages.append(source_language)
        return self.fountain

    def normalize_screenplay_pdf(self, pdf_bytes: bytes, on_progress=None, deadline=None) -> str:
        self.calls.append("normalize_screenplay_pdf")
        return self.fountain

    def analyze(
        self,
        fountain: str,
        on_progress=None,
        *,
        processing_deadline: float | None = None,
        source_filename: str | None = None,
        expected_characters: list[str] | None = None,
        source_language: str = "und",
    ) -> CanonicalAnalysis:
        self.calls.append("analyze")
        self.analyze_languages.append(source_language)
        from narravant.services.vertex_analysis import CanonicalAnalysis

        return CanonicalAnalysis(
            metadata={
                "title": "森の石松",
                "logline": "Logline",
                "synopsis": "Synopsis",
                "theme_setting": "Theme",
            },
            emotion_arc={
                "valence": [4],
                "tension": [0],
                "characters": {"石松": [4]},
                "scene_mapping": [
                    {
                        "point_number": 1,
                        "start_scene_number": 1,
                        "end_scene_number": 1,
                        "representative_scene_number": 1,
                    }
                ],
                "valence_vector": [0.0] * 9 + [1.0],
            },
            characters=[
                {
                    "name": "石松",
                    "external_goal": "旅を続ける",
                    "internal_need": "義理を果たす",
                    "fear_or_cost": "命を落とす",
                    "obstacle": "刺客",
                    "choice": "立ち向かう",
                    "agency": "強い",
                    "goal_to_outcome": "無念の死",
                    "related_turning_points": [1],
                    "voice_traits": "若々しく威勢のいい侠客の声",
                }
            ],
            turning_points=[
                {
                    "tp_number": n,
                    "label": [
                        "INCITING_INCIDENT",
                        "LOCK_IN",
                        "FIRST_CULMINATION",
                        "MAIN_CULMINATION",
                        "THIRD_ACT_TWIST",
                    ][n - 1],
                    "availability": "identified",
                    "scene_number": 1,
                    "change": f"Change {n}",
                    "involved_characters": [
                        {
                            "name": "石松",
                            "goal": "旅を続ける",
                            "conflict": "敵の出現",
                            "choice": "立ち向かう",
                            "action": "戦う",
                            "change": "決意を新たにする",
                        }
                    ],
                    "reason": None,
                }
                for n in range(1, 6)
            ],
            narrator={
                "voice_traits": "講談調の朗々とした語り口",
            },
        )


def _service_with_adapter(
    adapter: FakeNarrativeAdapter | None = None,
    analyzer: FakeDocumentAnalyzer | None = None,
) -> tuple[ImportService, DocumentRepository, InMemoryScriptStorageClient, TaskManager]:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient("test-narravant-bucket")
    tasks = TaskManager(TaskRepository(db), ephemeral_events=EphemeralTaskEvents())
    service = ImportService(
        repository,
        storage,
        tasks,
        ImportDraftStore(3600),
        analyzer or FakeDocumentAnalyzer(),
        adapter or FakeNarrativeAdapter(),
        processing_timeout_seconds=60,
        max_size_bytes=1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
    )
    return service, repository, storage, tasks


def test_import_routes_screenplay_and_prose_correctly() -> None:
    # 1. .fountain -> normalize_screenplay_text -> analyze (no adapter)
    adapter = FakeNarrativeAdapter()
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    content = b"Title: Script\n\nINT. SCENE - DAY #1#\n\nAction.\n"
    imported = validate_import(
        "test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)
    assert analyzer.calls == ["normalize_screenplay_text", "analyze"]
    assert adapter.calls == []

    # 2. .fdx -> normalize_screenplay_text -> analyze (no adapter)
    adapter = FakeNarrativeAdapter()
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    fdx_content = (
        b"<FinalDraft><Content><Paragraph Type='Scene Heading'>"
        b"<Text>INT. SCENE - DAY</Text></Paragraph></Content></FinalDraft>"
    )
    imported = validate_import(
        "test.fdx",
        fdx_content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, fdx_content)
    assert analyzer.calls == ["normalize_screenplay_text", "analyze"]
    assert adapter.calls == []

    # 3. .txt (SCREENPLAY) -> classify_text -> normalize_screenplay_text -> analyze
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.SCREENPLAY)
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    txt_content = b"INT. SCENE - DAY\n\nAction."
    imported = validate_import(
        "test.txt",
        txt_content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, txt_content)
    assert adapter.calls == ["classify_text"]
    assert analyzer.calls == ["normalize_screenplay_text", "analyze"]

    # 4. .txt (NARRATIVE_PROSE) -> classify_text -> adapt_text -> analyze
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.NARRATIVE_PROSE)
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    prose_content = "ある日、主人公は歩いていた。".encode()
    imported = validate_import(
        "novel.txt",
        prose_content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, prose_content)
    assert adapter.calls == ["classify_text", "adapt_text"]
    assert analyzer.calls == ["analyze"]

    # 5. .pdf (SCREENPLAY) -> classify_pdf -> normalize_screenplay_pdf -> analyze
    adapter = FakeNarrativeAdapter(pdf_kind=SourceKind.SCREENPLAY)
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    pdf_stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_stream)
    pdf_bytes = pdf_stream.getvalue()
    imported = validate_import(
        "script.pdf",
        pdf_bytes,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, pdf_bytes)
    assert adapter.calls == ["classify_pdf"]
    assert analyzer.calls == ["normalize_screenplay_pdf", "analyze"]

    # 6. .pdf (NARRATIVE_PROSE) -> classify_pdf -> adapt_pdf -> analyze
    adapter = FakeNarrativeAdapter(pdf_kind=SourceKind.NARRATIVE_PROSE)
    analyzer = FakeDocumentAnalyzer()
    service, _, _, tasks = _service_with_adapter(adapter, analyzer)
    imported = validate_import(
        "novel.pdf",
        pdf_bytes,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, pdf_bytes)
    assert adapter.calls == ["classify_pdf", "adapt_pdf"]
    assert analyzer.calls == ["analyze"]


def test_import_inconclusive_pdf_and_failed_adaptation_emit_terminal_error_without_persistence() -> None:
    pdf_stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_stream)
    pdf_bytes = pdf_stream.getvalue()

    # Case A: SOURCE_CLASSIFICATION_INCONCLUSIVE
    class InconclusiveAdapter(FakeNarrativeAdapter):
        def classify_pdf(self, pdf_bytes: bytes, on_progress=None, deadline=None) -> SourceKind:
            raise NarrativeAdaptationError(
                "SOURCE_CLASSIFICATION_INCONCLUSIVE",
                "文書の分類を特定できませんでした。",
            )

    service, repo, storage, tasks = _service_with_adapter(InconclusiveAdapter())
    imported = validate_import(
        "inconclusive.pdf",
        pdf_bytes,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, pdf_bytes)

    event = tasks.events_after(task_id, 0)[-1]
    assert event.event_type == "error"
    assert event.payload["code"] == "SOURCE_CLASSIFICATION_INCONCLUSIVE"
    assert event.payload["retryable"] is False
    assert repo.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}
    persisted = [e.payload for e in tasks.events_after(task_id, 0)]
    assert all("source_fountain" not in json.dumps(p) for p in persisted)

    # Case B: ADAPTATION_VERIFICATION_FAILED
    class FailedAdaptationAdapter(FakeNarrativeAdapter):
        def adapt_text(
            self,
            text: str,
            on_progress=None,
            ensure_not_cancelled=None,
            deadline=None,
            *,
            source_language: str = "und",
        ) -> str:
            raise NarrativeAdaptationError(
                "ADAPTATION_VERIFICATION_FAILED",
                "シーン再生成の上限に達しました。",
            )

    service, repo, storage, tasks = _service_with_adapter(FailedAdaptationAdapter())
    prose_bytes = "原作小説本文".encode()
    imported = validate_import(
        "prose.txt",
        prose_bytes,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, prose_bytes)

    event = tasks.events_after(task_id, 0)[-1]
    assert event.event_type == "error"
    assert event.payload["code"] == "ADAPTATION_VERIFICATION_FAILED"
    assert event.payload["retryable"] is False
    assert repo.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}
    persisted = [e.payload for e in tasks.events_after(task_id, 0)]
    assert all("source_fountain" not in json.dumps(p) for p in persisted)


def test_compose_enriched_fountain_language_headings():
    from narravant.services.ingestion import compose_enriched_fountain

    # Japanese input
    ja_analysis = CanonicalAnalysis(
        metadata={
            "title": "走れメロス",
            "logline": "メロスは激怒した。",
            "synopsis": "処刑台に向かうメロス。",
            "theme_setting": "信義。根拠は第1シーン",
        },
        emotion_arc={"valence": [4], "tension": [0], "characters": {}},
        characters=[],
        turning_points=[],
    )
    ja_fountain = "INT. ROOM - DAY #1#\n\nメロスは走る。"
    ja_result = compose_enriched_fountain(ja_fountain, ja_analysis)
    assert "# テーマ設定" not in ja_result
    assert "# スクリプト" not in ja_result
    assert "# Theme Setting" not in ja_result
    assert "# Script" not in ja_result

    # English input
    en_analysis = CanonicalAnalysis(
        metadata={
            "title": "A Christmas Carol",
            "logline": "Scrooge is visited by three ghosts.",
            "synopsis": "Ebenezer Scrooge learns kindness.",
            "theme_setting": "Dominant theme candidates are redemption and charity. Evidence: Scene 1.",
        },
        emotion_arc={"valence": [4], "tension": [0], "characters": {}},
        characters=[],
        turning_points=[],
    )
    en_fountain = "INT. OFFICE - DAY #1#\n\nScrooge counts his money."
    en_result = compose_enriched_fountain(en_fountain, en_analysis)
    assert "# Theme Setting" not in en_result
    assert "# Script" not in en_result
    assert "# テーマ設定" not in en_result
    assert "# スクリプト" not in en_result


def test_import_draft_carries_voice_traits_from_analyzer() -> None:
    analyzer = FakeDocumentAnalyzer()
    service, _, _, _ = _service_with_adapter(analyzer=analyzer)
    content = b"Title: Script\n\nINT. SCENE - DAY #1#\n\nAction.\n"
    imported = validate_import(
        "test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)

    draft = service.drafts.get(task_id)
    assert draft is not None
    assert draft.structured["narrator"]["voice_traits"] == "講談調の朗々とした語り口"
    assert draft.structured["analysis"]["characters"][0]["voice_traits"] == "若々しく威勢のいい侠客の声"

    res = draft_response(draft)
    assert res.narrator.voice_traits == "講談調の朗々とした語り口"
    assert res.analysis.characters[0].voice_traits == "若々しく威勢のいい侠客の声"


@pytest.mark.asyncio
async def test_import_and_save_pipeline_with_local_storage_client(tmp_path: Path) -> None:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repository = DocumentRepository(db)
    repository.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = LocalScriptStorageClient(tmp_path)
    tasks = TaskManager(TaskRepository(db), ephemeral_events=EphemeralTaskEvents())
    analyzer = FakeDocumentAnalyzer()
    adapter = FakeNarrativeAdapter()
    service = ImportService(
        repository,
        storage,
        tasks,
        ImportDraftStore(3600),
        analyzer,
        adapter,
        processing_timeout_seconds=60,
        max_size_bytes=1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
    )

    content = b"Title: Local Test\n\nINT. SCENE - DAY #1#\n\nAction.\n"
    imported = validate_import(
        "local_test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)

    draft = service.drafts.get(task_id)
    assert draft is not None
    assert draft.structured["narrator"]["voice_traits"] == "講談調の朗々とした語り口"
    assert draft.structured["analysis"]["characters"][0]["voice_traits"] == "若々しく威勢のいい侠客の声"

    payload = ImportDraftSaveRequest(
        title="Local Saved Script",
        fountain_text=str(draft.structured["source_fountain"]),
        metadata=deepcopy(draft.structured["metadata"]),
        analysis=deepcopy(draft.structured["analysis"]),
        emotion_arc=deepcopy(draft.structured["emotion_arc"]),
        narrator=deepcopy(draft.structured["narrator"]),
        voice_assignments=deepcopy(draft.structured.get("voice_assignments", [])),
    )
    settings = SimpleNamespace(
        gemini_processing_timeout_seconds=60,
        vertex_ai_processing_timeout_seconds=60,
        file_import_max_size_mb=1,
        file_import_pdf_max_pages=100,
        file_import_fdx_max_depth=32,
        file_import_txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
        sse_poll_interval_milliseconds=100,
        file_import_draft_ttl_seconds=3600,
    )
    save_res = await save_import_draft(
        task_id,
        payload,
        repository,
        storage,
        tasks,
        service.drafts,
        analyzer,
        adapter,
        settings,
        CurrentUser("owner-1", "owner@example.test"),
    )

    doc_id = save_res.document_id
    assert doc_id == draft.document_id
    assert repository.get_document(doc_id) is not None

    local_script_path = tmp_path / "scripts" / doc_id / "v1.json"
    assert local_script_path.exists()

    stored_json, _ = storage.read_structured_script(doc_id, 1)
    assert stored_json["metadata"]["title"] == "Local Saved Script"
    assert stored_json["narrator"]["voice_traits"] == "講談調の朗々とした語り口"
    assert stored_json["analysis"]["characters"][0]["voice_traits"] == "若々しく威勢のいい侠客の声"


def test_import_service_propagates_source_language() -> None:
    analyzer = FakeDocumentAnalyzer()
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.SCREENPLAY)
    service, _repo, _storage, _tasks = _service_with_adapter(adapter, analyzer=analyzer)

    en_screenplay = (
        b"Title: Storm Cabin\n\n"
        b"INT. CABIN - DAY #1#\n\n"
        b"@MAYA\n"
        b"The river rose, and she knew that her friend must find shelter with the group.\n"
    )
    imported_en = validate_import(
        "storm.fountain",
        en_screenplay,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_en = service.start("owner-1", imported_en)
    service.run(task_en, en_screenplay)
    assert analyzer.normalize_languages == ["en"]
    assert analyzer.analyze_languages == ["en"]

    analyzer.normalize_languages.clear()
    analyzer.analyze_languages.clear()
    ja_screenplay = (
        "Title: 嵐の小屋\n\n"
        "INT. CABIN - DAY #1#\n\n"
        "@マヤ\n"
        "川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。\n"
    ).encode()
    imported_ja = validate_import(
        "storm_ja.fountain",
        ja_screenplay,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_ja = service.start("owner-1", imported_ja)
    service.run(task_ja, ja_screenplay)
    assert analyzer.normalize_languages == ["ja"]
    assert analyzer.analyze_languages == ["ja"]

    # Prose novel import test
    adapter_prose = FakeNarrativeAdapter(text_kind=SourceKind.NARRATIVE_PROSE)
    analyzer_prose = FakeDocumentAnalyzer()
    service_prose, _, _, _ = _service_with_adapter(adapter_prose, analyzer=analyzer_prose)

    en_novel = (
        b"The river rose, and Maya knew that her friend must find shelter with the group. "
        b"They were walking toward a small cabin when the wind broke a branch above their heads. "
        b"She held the map while Jon searched for a safer path. Their plan was to wait inside, "
        b"but the door was locked and they had to find another way before the storm arrived."
    )
    imported_en_novel = validate_import(
        "novel_en.txt",
        en_novel,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_en_novel = service_prose.start("owner-1", imported_en_novel)
    service_prose.run(task_en_novel, en_novel)
    assert adapter_prose.adapt_languages == ["en"]
    assert analyzer_prose.analyze_languages == ["en"]


@pytest.mark.parametrize(
    "invalid_cue_body",
    [
        "@MAYA stands beside @JON, watching the rain.\nThe shutter rattles.",
        "@MAYA sits on the wooden floor.\nThe shutter rattles.",
        "@MAYA\n(quietly)\n\n@JON\nHello.",
    ],
)
def test_import_fails_on_invalid_generated_cue(invalid_cue_body: str) -> None:
    analyzer = FakeDocumentAnalyzer()
    analyzer.fountain = f"Title: Test\n\nINT. CABIN - DAY #1#\n\n{invalid_cue_body}\n"
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.SCREENPLAY)
    service, repo, storage, tasks = _service_with_adapter(adapter, analyzer=analyzer)

    content = b"Title: Test\n\nINT. CABIN - DAY #1#\n\n@MAYA\nHello.\n"
    imported = validate_import(
        "test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)

    events = tasks.events_after(task_id, 0)
    error = events[-1]
    assert error.event_type == "error"
    assert error.payload["code"] == "INVALID_SCRIPT_STRUCTURE"
    assert len(analyzer.analyze_languages) == 0
    assert not any(item.event_type == "completed" for item in events)
    assert service.drafts.get(task_id) is None
    assert repo.list_documents(limit=10, offset=0)[1] == 0
    assert storage._objects == {}


def test_import_sentence_in_cue_respects_known_source_speakers() -> None:
    analyzer = FakeDocumentAnalyzer()
    analyzer.fountain = "Title: Test\n\nINT. CABIN - DAY #1#\n\n@MAYA van Meer.\nI am here.\n"
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.SCREENPLAY)

    # 1. Known in source: accepted
    service, repo, storage, tasks = _service_with_adapter(adapter, analyzer=analyzer)
    content_with_known = b"Title: Test\n\nINT. CABIN - DAY #1#\n\n@MAYA van Meer.\nI am here.\n"
    imported_known = validate_import(
        "test.fountain",
        content_with_known,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id_1 = service.start("owner-1", imported_known)
    service.run(task_id_1, content_with_known)

    events_1 = tasks.events_after(task_id_1, 0)
    assert events_1[-1].event_type == "completed"
    assert len(analyzer.analyze_languages) == 1

    # 2. Unknown in source: rejected as sentence_in_cue -> INVALID_SCRIPT_STRUCTURE
    analyzer.analyze_languages.clear()
    service2, repo2, storage2, tasks2 = _service_with_adapter(adapter, analyzer=analyzer)
    content_without_known = b"Title: Test\n\nINT. CABIN - DAY #1#\n\n@OTHER\nHello.\n"
    imported_unknown = validate_import(
        "test.fountain",
        content_without_known,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id_2 = service2.start("owner-1", imported_unknown)
    service2.run(task_id_2, content_without_known)

    events_2 = tasks2.events_after(task_id_2, 0)
    assert events_2[-1].event_type == "error"
    assert events_2[-1].payload["code"] == "INVALID_SCRIPT_STRUCTURE"
    assert len(analyzer.analyze_languages) == 0


def test_import_fails_on_generated_speech_language_mismatch() -> None:
    analyzer = FakeDocumentAnalyzer()
    # Generated fountain has Japanese dialogue
    analyzer.fountain = (
        "Title: Storm Cabin\n\n"
        "INT. CABIN - DAY #1#\n\n"
        "@MAYA\n"
        "川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。\n"
    )
    adapter = FakeNarrativeAdapter(text_kind=SourceKind.SCREENPLAY)
    service, _repo, _storage, tasks = _service_with_adapter(adapter, analyzer=analyzer)

    # Source is English
    en_screenplay = (
        b"Title: Storm Cabin\n\n"
        b"INT. CABIN - DAY #1#\n\n"
        b"@MAYA\n"
        b"The river rose, and she knew that her friend must find shelter with the group.\n"
    )
    imported = validate_import(
        "storm.fountain",
        en_screenplay,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, en_screenplay)

    events = tasks.events_after(task_id, 0)
    error = events[-1]
    assert error.event_type == "error"
    assert error.payload["code"] == "IMPORT_FAILED"
    assert len(analyzer.analyze_languages) == 0


@pytest.mark.parametrize(
    ("filename", "ext", "is_pdf", "text_kind", "pdf_kind"),
    [
        ("script.fountain", ".fountain", False, SourceKind.SCREENPLAY, SourceKind.SCREENPLAY),
        ("script.fdx", ".fdx", False, SourceKind.SCREENPLAY, SourceKind.SCREENPLAY),
        ("screenplay.txt", ".txt", False, SourceKind.SCREENPLAY, SourceKind.SCREENPLAY),
        ("novel.txt", ".txt", False, SourceKind.NARRATIVE_PROSE, SourceKind.SCREENPLAY),
        ("screenplay.pdf", ".pdf", True, SourceKind.SCREENPLAY, SourceKind.SCREENPLAY),
        ("novel.pdf", ".pdf", True, SourceKind.SCREENPLAY, SourceKind.NARRATIVE_PROSE),
    ],
)
def test_all_import_formats_reject_invalid_cue_at_final_boundary(
    filename: str, ext: str, is_pdf: bool, text_kind: SourceKind, pdf_kind: SourceKind
) -> None:
    invalid_fountain = (
        "Title: Test\n\nINT. CABIN - DAY #1#\n\n"
        "@MAYA stands beside @JON, watching the rain.\nThe shutter rattles.\n"
    )
    analyzer = FakeDocumentAnalyzer()
    analyzer.fountain = invalid_fountain
    adapter = FakeNarrativeAdapter(text_kind=text_kind, pdf_kind=pdf_kind)
    adapter.fountain = invalid_fountain
    service, repo, storage, tasks = _service_with_adapter(adapter, analyzer=analyzer)

    if is_pdf:
        pdf_stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(pdf_stream)
        content = pdf_stream.getvalue()
    elif ext == ".fdx":
        content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<FinalDraft DocumentType="Script" Template="No" Version="1">\n'
            b'<Content><Paragraph Type="Scene Heading"><Text>INT. CABIN - DAY</Text></Paragraph>'
            b'<Paragraph Type="Character"><Text>MAYA</Text></Paragraph>'
            b'<Paragraph Type="Dialogue"><Text>Hello.</Text></Paragraph></Content></FinalDraft>'
        )
    else:
        content = b"Title: Test\n\nINT. CABIN - DAY #1#\n\n@MAYA\nHello.\n"

    imported = validate_import(
        filename,
        content,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)

    events = tasks.events_after(task_id, 0)
    assert events[-1].event_type == "error"
    assert events[-1].payload["code"] == "INVALID_SCRIPT_STRUCTURE"
    assert len(analyzer.analyze_languages) == 0
