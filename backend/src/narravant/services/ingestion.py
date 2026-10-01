"""Safe import validation, ephemeral draft creation, and explicit Save publishing."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Protocol

from charset_normalizer import from_bytes
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from narravant.api.schemas import (
    AnalysisSchema,
    EmotionArcSchema,
    ImportDraftResponse,
    ImportDraftSaveRequest,
    NarratorSchema,
    NativeDocumentSchema,
    NativeExchangeEnvelope,
    SceneSchema,
    VoiceAssignmentSchema,
)
from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.core.fountain import FountainParser, normalize_speaker_name
from narravant.core.valence_vector import default_valence_vectorizer
from narravant.db.database import DocumentRepository, OptimisticLockError
from narravant.domain.tasks import TERMINAL_TASK_STATUSES, TaskStatus
from narravant.services.narrative_adaptation import (
    NarrativeAdaptationError,
    SourceKind,
)
from narravant.services.tasks import TaskManager
from narravant.services.vertex_analysis import (
    TURNING_POINT_LABELS,
    CanonicalAnalysis,
    VertexStreamTimeoutError,
    is_transient_vertex_error,
)
from narravant.storage.gcs import GcsConflictError, ScriptStorageClient

logger = logging.getLogger(__name__)
ProgressReporter = Callable[[int], None]

SUPPORTED_EXTENSIONS = frozenset({".fountain", ".txt", ".pdf", ".fdx", ".json"})
SOURCE_MEDIA_TYPES = {
    ".fountain": "text/x-fountain",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".fdx": "application/vnd.finaldraft",
    ".json": "application/json",
}
PDF_SIGNATURE = b"%PDF-"
VERTEX_RATE_LIMIT_MESSAGE = (
    "生成AIのレート／クォータ制限により、文書のImportまたは分析に失敗しました。しばらく経ってから再度実行してください。"
)
VERTEX_TEMPORARY_SERVICE_MESSAGE = (
    "生成AIの一時的なサービス障害により、文書のImportまたは分析に失敗しました。しばらく経ってから再度実行してください。"
)
VERTEX_TIMEOUT_MESSAGE = (
    "生成AIからの応答がタイムアウトしたため、文書のImportまたは分析に失敗しました。"
    "しばらく経ってから再度実行してください。"
)


class ImportValidationError(ValueError):
    """A stable validation failure that never carries source content."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ImportDraftExpiredError(KeyError):
    """The process-local draft has elapsed and must be imported again."""


@dataclass(frozen=True)
class ValidatedImport:
    filename: str
    extension: str
    media_type: str
    text: str | None


def validate_import(
    filename: str,
    content: bytes,
    max_size_bytes: int,
    *,
    pdf_max_pages: int,
    fdx_max_depth: int,
    txt_minimum_confidence: float,
) -> ValidatedImport:
    """Validate bytes without storing source text, analysis, or document metadata."""
    safe_filename = Path(filename).name
    extension = Path(safe_filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ImportValidationError(
            "UNSUPPORTED_MEDIA_TYPE",
            "対応していないファイル形式です。.fountain、.txt、.pdf、.fdx、.jsonを選択してください。",
        )
    if not content:
        raise ImportValidationError("EMPTY_FILE", "空のファイルはImportできません。")
    if len(content) > max_size_bytes:
        raise ImportValidationError("FILE_TOO_LARGE", "ファイルサイズが上限を超えています。", status_code=413)
    if extension == ".pdf":
        _preflight_pdf(content, pdf_max_pages)
        text = None
    elif extension == ".fdx":
        text = _extract_fdx_fountain(content, max_depth=fdx_max_depth)
    elif extension == ".fountain":
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ImportValidationError("INVALID_ENCODING", "FountainはUTF-8で保存してください。") from exc
    elif extension == ".txt":
        text = _decode_txt(content, txt_minimum_confidence)
    else:
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONはUTF-8で保存してください。") from exc
    if text is not None and not text.strip():
        raise ImportValidationError("EMPTY_FILE", "空のファイルはImportできません。")
    return ValidatedImport(safe_filename, extension, SOURCE_MEDIA_TYPES[extension], text)


def _decode_txt(content: bytes, minimum_confidence: float) -> str:
    if b"\x00" in content:
        raise ImportValidationError("INVALID_ENCODING", "テキストにNULバイトを含めることはできません。")
    match = from_bytes(content).best()
    if match is None or not match.encoding or 1.0 - float(match.chaos) < minimum_confidence:
        raise ImportValidationError("INVALID_ENCODING", "テキストの文字コードを安全に判定できませんでした。")
    try:
        return content.decode(match.encoding, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise ImportValidationError("INVALID_ENCODING", "検出した文字コードでテキストを読み取れませんでした。") from exc


def _preflight_pdf(content: bytes, max_pages: int) -> None:
    if not content.startswith(PDF_SIGNATURE):
        raise ImportValidationError("MALFORMED_PDF", "PDFファイルとして読み取れませんでした。")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ImportValidationError("MALFORMED_PDF", "暗号化PDFはImportできません。")
        if len(reader.pages) > max_pages:
            raise ImportValidationError("MALFORMED_PDF", "PDFのページ数が上限を超えています。")
    except ImportValidationError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError) as exc:
        raise ImportValidationError("MALFORMED_PDF", "PDFファイルとして読み取れませんでした。") from exc


def _extract_fdx_fountain(content: bytes, *, max_depth: int) -> str:
    try:
        root = ElementTree.fromstring(content)
    except (DefusedXmlException, ElementTree.ParseError) as exc:
        raise ImportValidationError("MALFORMED_FDX", "FDXファイルを読み取れませんでした。") from exc
    if _local_name(root.tag) != "FinalDraft" or _xml_depth(root) > max_depth:
        raise ImportValidationError("MALFORMED_FDX", "FDXファイルを読み取れませんでした。")
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "Paragraph":
            continue
        text = "".join(paragraph.itertext()).strip()
        if not text:
            continue
        paragraph_type = (paragraph.attrib.get("Type") or "").casefold()
        if paragraph_type == "character":
            text = f"@{text.lstrip('@').strip()}"
        elif paragraph_type == "parenthetical" and not text.startswith("("):
            text = f"({text})"
        paragraphs.append(text)
    if not paragraphs:
        raise ImportValidationError("EMPTY_FILE", "FDXファイルに本文がありません。")
    return "\n\n".join(paragraphs)


def _xml_depth(element: object) -> int:
    maximum = 0
    pending = [(element, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        pending.extend((child, depth + 1) for child in current)  # type: ignore[union-attr]
    return maximum


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


class DocumentAnalyzer(Protocol):
    def normalize_screenplay_text(
        self,
        source_text: str,
        on_progress: ProgressReporter,
        *,
        deadline: float | None = None,
    ) -> str: ...

    def normalize_screenplay_pdf(
        self,
        source_pdf: bytes,
        on_progress: ProgressReporter,
        *,
        deadline: float | None = None,
    ) -> str: ...

    def analyze(
        self,
        source_fountain: str,
        on_progress: ProgressReporter,
        *,
        processing_deadline: float | None = None,
        source_filename: str | None = None,
        expected_characters: list[str] | None = None,
    ) -> CanonicalAnalysis: ...


class NarrativeAdapter(Protocol):
    def classify_text(
        self,
        source_text: str,
        *,
        on_progress: ProgressReporter | None = None,
        deadline: float | None = None,
    ) -> SourceKind: ...

    def classify_pdf(
        self,
        source_pdf: bytes,
        *,
        on_progress: ProgressReporter | None = None,
        deadline: float | None = None,
    ) -> SourceKind: ...

    def adapt_text(
        self,
        source_text: str,
        *,
        on_progress: Callable[[str, int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> str: ...

    def adapt_pdf(
        self,
        source_pdf: bytes,
        *,
        on_progress: Callable[[str, int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class ImportDraft:
    task_id: str
    owner_user_id: str
    document_id: str
    expected_version: int | None
    structured: dict[str, object]
    expires_at: float


class ImportDraftStore:
    """The only pre-Save storage for import bodies and analysis results."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._drafts: dict[str, ImportDraft] = {}
        self._expired_task_ids: set[str] = set()

    def put(self, draft: ImportDraft) -> None:
        self._drafts[draft.task_id] = draft

    def get(self, task_id: str) -> ImportDraft | None:
        draft = self._drafts.get(task_id)
        if draft is not None and monotonic() >= draft.expires_at:
            self.discard(task_id)
            self._expired_task_ids.add(task_id)
            return None
        return draft

    def was_expired(self, task_id: str) -> bool:
        return task_id in self._expired_task_ids

    def discard(self, task_id: str) -> None:
        self._drafts.pop(task_id, None)


class ImportService:
    """Builds drafts; only ``save`` writes GCS, documents, versions, Search text, or vectors."""

    def __init__(
        self,
        repository: DocumentRepository,
        storage: ScriptStorageClient,
        tasks: TaskManager,
        drafts: ImportDraftStore,
        analyzer: DocumentAnalyzer,
        adapter: NarrativeAdapter,
        *,
        processing_timeout_seconds: int,
        max_size_bytes: int,
        pdf_max_pages: int,
        fdx_max_depth: int,
        txt_minimum_confidence: float,
        emotion_arc_max_points: int,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.tasks = tasks
        self.drafts = drafts
        self.analyzer = analyzer
        self.adapter = adapter
        self.processing_timeout_seconds = processing_timeout_seconds
        self.max_size_bytes = max_size_bytes
        self.pdf_max_pages = pdf_max_pages
        self.fdx_max_depth = fdx_max_depth
        self.txt_minimum_confidence = txt_minimum_confidence
        self.emotion_arc_max_points = emotion_arc_max_points

    def start(self, owner_user_id: str, imported: ValidatedImport) -> str:
        document_id = str(uuid.uuid4())
        return self.tasks.create(
            owner_user_id,
            "document_import",
            document_id,
            {"filename": imported.filename, "document_id": document_id},
        )

    def run(self, task_id: str, content: bytes) -> None:
        task = self.tasks.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        payload = json.loads(task["payload_json"])
        document_id = str(payload["document_id"])
        try:
            self.tasks.start(task_id)
            imported = validate_import(
                str(payload["filename"]),
                content,
                self.max_size_bytes,
                pdf_max_pages=self.pdf_max_pages,
                fdx_max_depth=self.fdx_max_depth,
                txt_minimum_confidence=self.txt_minimum_confidence,
            )
            if imported.extension == ".json":
                self.tasks.publish(
                    task_id,
                    "progress",
                    {
                        "phase": "structuring",
                        "percentage": 10,
                        "message": "Native JSONを検証しています。",
                    },
                )
                structured = native_exchange_to_v1(
                    _required_text(imported),
                    document_id,
                    task["owner_user_id"],
                    imported,
                    max_points=self.emotion_arc_max_points,
                )
            else:
                deadline = monotonic() + self.processing_timeout_seconds
                if imported.extension in {".fountain", ".fdx"}:
                    self.tasks.publish(
                        task_id,
                        "progress",
                        {
                            "phase": "writing",
                            "percentage": 20,
                            "message": "脚本を正規化しています。",
                        },
                    )
                    source_fountain = self.analyzer.normalize_screenplay_text(
                        _required_text(imported),
                        self._stream_reporter(task_id, "writing", 20),
                        deadline=deadline,
                    )
                elif imported.extension == ".txt":
                    source_fountain = self._route_text_import(task_id, imported, deadline)
                elif imported.extension == ".pdf":
                    source_fountain = self._route_pdf_import(task_id, content, deadline)
                else:
                    raise ImportValidationError("UNSUPPORTED_MEDIA_TYPE", "未対応のファイル形式です。")

                self._ensure_not_cancelled(task_id)
                parsed_source = FountainParser.parse(source_fountain)
                if parsed_source.scene_count() < 1 or parsed_source.utterance_count() < 1:
                    raise ImportValidationError(
                        "INVALID_SCRIPT_STRUCTURE",
                        "生成された脚本にシーンまたは発話がありません。",
                    )
                self.tasks.publish(
                    task_id,
                    "progress",
                    {
                        "phase": "analyzing",
                        "percentage": 65,
                        "message": "構造と感情アークを分析しています。",
                    },
                )
                analysis = self.analyzer.analyze(
                    source_fountain,
                    self._stream_reporter(task_id, "analyzing", 65),
                    processing_deadline=deadline,
                    source_filename=imported.filename,
                )
                structured = canonical_analysis_to_v1(
                    source_fountain,
                    analysis,
                    document_id,
                    task["owner_user_id"],
                    imported,
                    max_points=self.emotion_arc_max_points,
                )
            self._ensure_not_cancelled(task_id)
            draft = ImportDraft(
                task_id,
                task["owner_user_id"],
                document_id,
                None,
                structured,
                monotonic() + self.drafts.ttl_seconds,
            )
            self.drafts.put(draft)
            self.tasks.complete_import_draft(
                task_id,
                {"import_draft": draft_response(draft).model_dump(mode="json")},
                self.drafts.ttl_seconds,
            )
            logger.info(
                "Import draft completed task_id=%s document_id=%s format=%s",
                task_id,
                document_id,
                imported.extension,
            )
        except ImportValidationError as exc:
            self._fail(task_id, exc.code, str(exc), retryable=False)
        except NarrativeAdaptationError as exc:
            logger.error(
                "Import adaptation error task_id=%s code=%s message=%s",
                task_id,
                exc.code,
                str(exc),
            )
            self._fail(task_id, exc.code, str(exc), retryable=False)
        except (OptimisticLockError, GcsConflictError):
            self._fail(
                task_id,
                "CONFLICT",
                "文書が更新されました。再読み込みして再試行してください。",
                retryable=True,
            )
        except VertexStreamTimeoutError:
            self._fail(task_id, "IMPORT_FAILED", VERTEX_TIMEOUT_MESSAGE, retryable=True)
        except Exception as exc:
            if is_transient_vertex_error(exc):
                logger.error(
                    "Import task failed task_id=%s error_code=%s retryable=true",
                    task_id,
                    getattr(exc, "code", None),
                )
                message = (
                    VERTEX_RATE_LIMIT_MESSAGE if getattr(exc, "code", None) == 429 else VERTEX_TEMPORARY_SERVICE_MESSAGE
                )
                self._fail(task_id, "IMPORT_FAILED", message, retryable=True)
            else:
                logger.exception("Import task failed task_id=%s", task_id, exc_info=exc)
                self._fail(
                    task_id,
                    "IMPORT_FAILED",
                    "文書のImportまたは分析に失敗しました。",
                    retryable=False,
                )

    def _route_text_import(self, task_id: str, imported: ValidatedImport, deadline: float) -> str:
        text = _required_text(imported)
        self.tasks.publish(
            task_id,
            "progress",
            {
                "phase": "classifying",
                "percentage": 10,
                "message": "テキストの種別を判定しています。",
            },
        )
        kind = self.adapter.classify_text(
            text,
            on_progress=self._stream_reporter(task_id, "classifying", 10),
            deadline=deadline,
        )
        self._ensure_not_cancelled(task_id)
        if kind == SourceKind.SCREENPLAY:
            self.tasks.publish(
                task_id,
                "progress",
                {
                    "phase": "writing",
                    "percentage": 20,
                    "message": "脚本を正規化しています。",
                },
            )
            return self.analyzer.normalize_screenplay_text(
                text,
                on_progress=self._stream_reporter(task_id, "writing", 20),
                deadline=deadline,
            )
        return self.adapter.adapt_text(
            text,
            on_progress=self._adaptation_reporter(task_id),
            ensure_not_cancelled=lambda: self._ensure_not_cancelled(task_id),
            deadline=deadline,
        )

    def _route_pdf_import(self, task_id: str, content: bytes, deadline: float) -> str:
        self.tasks.publish(
            task_id,
            "progress",
            {
                "phase": "classifying",
                "percentage": 10,
                "message": "PDFの種別を判定しています。",
            },
        )
        kind = self.adapter.classify_pdf(
            content,
            on_progress=self._stream_reporter(task_id, "classifying", 10),
            deadline=deadline,
        )
        self._ensure_not_cancelled(task_id)
        if kind == SourceKind.SCREENPLAY:
            self.tasks.publish(
                task_id,
                "progress",
                {
                    "phase": "writing",
                    "percentage": 20,
                    "message": "原PDFから脚本を正規化しています。",
                },
            )
            return self.analyzer.normalize_screenplay_pdf(
                content,
                on_progress=self._stream_reporter(task_id, "writing", 20),
                deadline=deadline,
            )
        return self.adapter.adapt_pdf(
            content,
            on_progress=self._adaptation_reporter(task_id),
            ensure_not_cancelled=lambda: self._ensure_not_cancelled(task_id),
            deadline=deadline,
        )

    def _adaptation_reporter(self, task_id: str) -> Callable[[str, int], None]:
        phase_info: dict[str, tuple[int, str]] = {
            "reading": (20, "物語を読み込んでいます。"),
            "building_plot_graph": (35, "因果プロットグラフを構築しています。"),
            "planning": (45, "脚色アウトラインを計画しています。"),
            "writing": (55, "シーンを生成しています。"),
            "verifying": (60, "シーンの整合性を検証しています。"),
        }

        def report(phase: str, _count: int) -> None:
            self._ensure_not_cancelled(task_id)
            percentage, message = phase_info.get(phase, (50, "処理中..."))
            self.tasks.publish(
                task_id,
                "progress",
                {
                    "phase": phase,
                    "percentage": percentage,
                    "message": message,
                },
            )

        return report

    def save(
        self,
        task_id: str,
        owner_user_id: str,
        edits: ImportDraftSaveRequest | None = None,
    ) -> tuple[str, int, int]:
        draft = self.drafts.get(task_id)
        if draft is None or draft.owner_user_id != owner_user_id:
            if self.drafts.was_expired(task_id):
                raise ImportDraftExpiredError(task_id)
            raise KeyError(task_id)
        structured = (
            _apply_import_draft_edits(draft.structured, edits, max_points=self.emotion_arc_max_points)
            if edits is not None
            else deepcopy(draft.structured)
        )
        metadata = structured["metadata"]  # type: ignore[assignment]
        title = str(metadata["title"]).strip()
        stored = self.storage.write_structured_script(draft.document_id, 1, structured, if_generation_match=0)
        try:
            search = self.storage.write_search_text(
                draft.document_id,
                str(structured["source_fountain"]),
                if_generation_match=0,
            )
            row = self.repository.create_document(
                draft.document_id,
                owner_user_id,
                title,
                stored.uri,
                stored.generation,
                stored.sha256,
                valence_vector=structured["emotion_arc"]["valence_vector"],  # type: ignore[index]
            )
        except Exception:
            try:
                self.storage.delete_uri(search.uri, search.generation)
            except (FileNotFoundError, GcsConflictError, UnboundLocalError):
                pass
            try:
                self.storage.delete_uri(stored.uri, stored.generation)
            except (FileNotFoundError, GcsConflictError):
                pass
            raise
        version_id = 1
        self.drafts.discard(task_id)
        return draft.document_id, version_id, int(row["version_id"])

    def _ensure_not_cancelled(self, task_id: str) -> None:
        task = self.tasks.repository.get(task_id)
        if task is not None and TaskStatus(task["status"]) is TaskStatus.CANCEL_REQUESTED:
            self.tasks.cancel(task_id, "Importをキャンセルしました。")
            raise RuntimeError("cancelled")

    def _fail(self, task_id: str, code: str, message: str, *, retryable: bool) -> None:
        task = self.tasks.repository.get(task_id)
        if task is not None and TaskStatus(task["status"]) not in TERMINAL_TASK_STATUSES:
            self.tasks.fail(task_id, {"code": code, "message": message, "retryable": retryable})
        self.drafts.discard(task_id)

    def _stream_reporter(self, task_id: str, phase: str, percentage: int) -> ProgressReporter:
        def report(received: int) -> None:
            # Vertex invokes this once per received chunk; cancellation is observed
            # before the next chunk is accepted or persisted as task progress.
            self._ensure_not_cancelled(task_id)
            self.tasks.publish(
                task_id,
                "progress",
                {
                    "phase": phase,
                    "percentage": percentage,
                    "message": "生成AI応答を受信しています。",
                    "received_characters": received,
                },
            )

        return report


def _required_text(imported: ValidatedImport) -> str:
    if imported.text is None:
        raise ValueError("テキスト本文がありません。")
    return imported.text


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONに重複キーがあります。")
        result[key] = value
    return result


def native_exchange_to_v1(
    text: str,
    document_id: str,
    owner_user_id: str,
    imported: ValidatedImport,
    *,
    max_points: int,
) -> dict[str, object]:
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
        envelope = NativeExchangeEnvelope.model_validate(raw)
    except (json.JSONDecodeError, ImportValidationError, ValueError) as exc:
        raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONの形式または検証条件が不正です。") from exc
    document = envelope.document
    parsed = FountainParser.parse(document.source_fountain)
    titles = (document.title, document.metadata.title, parsed.metadata.title)
    if not parsed.scenes or not parsed.metadata.title or len({_normalized_title(value) for value in titles}) != 1:
        raise ImportValidationError(
            "INVALID_NATIVE_DOCUMENT",
            "Native JSONのタイトルまたはFountain本文が一致しません。",
        )
    _validate_native_cross_fields(document, parsed.scene_count(), max_points=max_points)
    analysis = {
        "status": "completed",
        "turning_points": [
            {**point.model_dump(), "label": TURNING_POINT_LABELS[point.tp_number]}
            for point in document.analysis.turning_points
        ],
        "characters": [character.model_dump() for character in document.analysis.characters],
    }
    emotion_arc = {
        "valence": document.emotion_arc.valence,
        "tension": document.emotion_arc.tension,
        "characters": document.emotion_arc.characters,
        "scene_mapping": scene_mapping_payload(parsed.scene_count(), max_points),
        "valence_vector": default_valence_vectorizer.vectorize(document.emotion_arc.valence),
    }
    structured = parsed.to_v1_json(
        document_id,
        owner_user_id,
        {
            "filename": imported.filename,
            "media_type": imported.media_type,
            "sha256": sha256(parsed.source_fountain.encode()).hexdigest(),
        },
        analysis,
        emotion_arc=emotion_arc,
        narrator=document.narrator.model_dump(),
        voice_assignments=[va.model_dump() for va in document.voice_assignments],
    )
    structured["metadata"] = document.metadata.model_dump()
    return structured


def _normalized_title(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _validate_native_cross_fields(document: NativeDocumentSchema, scene_count: int, *, max_points: int) -> None:
    points = document.analysis.turning_points
    if sorted(point.tp_number for point in points) != [1, 2, 3, 4, 5] or any(
        point.availability == "identified" and point.scene_number > scene_count for point in points
    ):
        raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONの転換点が不正です。")
    names = [character.name for character in document.analysis.characters]
    if len(names) != len(set(names)) or set(names) != set(document.emotion_arc.characters):
        raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONのキャラクター集合が一致しません。")
    arcs = [
        document.emotion_arc.valence,
        document.emotion_arc.tension,
        *document.emotion_arc.characters.values(),
    ]
    expected_arc_length = min(scene_count, max_points)
    if any(len(values) != expected_arc_length for values in arcs):
        raise ImportValidationError(
            "INVALID_NATIVE_DOCUMENT",
            "Native JSONの感情アーク長が不正です。",
        )
    if (
        any(not 1 <= value <= 7 for value in document.emotion_arc.valence)
        or any(not -3 <= value <= 3 for value in document.emotion_arc.tension)
        or any(not 0 <= value <= 7 for values in document.emotion_arc.characters.values() for value in values)
    ):
        raise ImportValidationError("INVALID_NATIVE_DOCUMENT", "Native JSONの感情アーク値が範囲外です。")


def canonical_analysis_to_v1(
    source_fountain: str,
    analysis: CanonicalAnalysis,
    document_id: str,
    owner_user_id: str,
    imported: ValidatedImport,
    *,
    max_points: int,
) -> dict[str, object]:
    enriched = compose_enriched_fountain(source_fountain, analysis)
    parsed = FountainParser.parse(enriched)
    if parsed.scene_count() < 1 or parsed.utterance_count() < 1:
        raise ImportValidationError("INVALID_SCRIPT_STRUCTURE", "分析済みFountainにシーンまたは発話がありません。")
    validate_canonical_analysis(analysis, parsed.scene_count(), max_points=max_points)
    emotion_arc = dict(analysis.emotion_arc)
    emotion_arc["valence_vector"] = default_valence_vectorizer.vectorize(emotion_arc["valence"])
    narrator = getattr(analysis, "narrator", None) or {"voice_traits": ""}
    structured = parsed.to_v1_json(
        document_id,
        owner_user_id,
        {
            "filename": imported.filename,
            "media_type": imported.media_type,
            "sha256": sha256(parsed.source_fountain.encode()).hexdigest(),
        },
        {
            "status": "completed",
            "turning_points": analysis.turning_points,
            "characters": analysis.characters,
        },
        emotion_arc=emotion_arc,
        narrator=narrator,
    )
    structured["metadata"] = analysis.metadata
    return structured


def _clean_character_name(name: str) -> str:
    return normalize_speaker_name(name)


def align_character_arcs(
    generated_arcs: dict[str, list[int]],
    expected_names: list[str],
    scene_count: int,
    fallback_arc: list[int] | None = None,
) -> dict[str, list[int]]:
    """Align generated character emotion arcs to the expected character names."""
    if fallback_arc is None or len(fallback_arc) != scene_count:
        fallback_arc = [0] * scene_count

    result: dict[str, list[int]] = {}
    remaining_generated = dict(generated_arcs)
    remaining_expected = list(expected_names)

    # 1. Exact match
    for exp_name in list(remaining_expected):
        if exp_name in remaining_generated:
            arc = remaining_generated.pop(exp_name)
            result[exp_name] = arc if isinstance(arc, list) and len(arc) == scene_count else fallback_arc
            remaining_expected.remove(exp_name)

    # 2. Normalized base name match (without parenthesized aliases)
    if remaining_expected and remaining_generated:
        for exp_name in list(remaining_expected):
            exp_clean = _clean_character_name(exp_name)
            matched_gen_key = None
            for gen_name in remaining_generated:
                gen_clean = _clean_character_name(gen_name)
                if exp_clean and gen_clean and exp_clean == gen_clean:
                    matched_gen_key = gen_name
                    break
            if matched_gen_key is not None:
                arc = remaining_generated.pop(matched_gen_key)
                result[exp_name] = arc if isinstance(arc, list) and len(arc) == scene_count else fallback_arc
                remaining_expected.remove(exp_name)

    # 3. Substring / containment match
    if remaining_expected and remaining_generated:
        for exp_name in list(remaining_expected):
            exp_clean = _clean_character_name(exp_name)
            matched_gen_key = None
            for gen_name in remaining_generated:
                gen_clean = _clean_character_name(gen_name)
                if exp_clean and gen_clean and (exp_clean in gen_clean or gen_clean in exp_clean):
                    matched_gen_key = gen_name
                    break
            if matched_gen_key is not None:
                arc = remaining_generated.pop(matched_gen_key)
                result[exp_name] = arc if isinstance(arc, list) and len(arc) == scene_count else fallback_arc
                remaining_expected.remove(exp_name)

    # 4. Fill any remaining expected characters with fallback
    for exp_name in remaining_expected:
        result[exp_name] = fallback_arc

    return result


def validate_canonical_analysis(analysis: CanonicalAnalysis, scene_count: int, *, max_points: int) -> None:
    """Validate an LLM result before it can become a draft or reanalysis response."""
    points = analysis.turning_points
    if sorted(point.get("tp_number") for point in points) != [1, 2, 3, 4, 5]:
        raise ValueError("分析結果の5転換点が不完全です。")
    for point in points:
        availability = point.get("availability")
        if availability == "identified":
            scene_number = point.get("scene_number")
            if (
                not isinstance(scene_number, int)
                or isinstance(scene_number, bool)
                or not 1 <= scene_number <= scene_count
            ):
                raise ValueError("分析結果の転換点シーン番号が不正です。")
        elif availability == "not_applicable":
            if point.get("scene_number") is not None or point.get("change") is not None:
                raise ValueError("分析結果の転換点（該当なし）にシーン番号または変化が含まれています。")
            if point.get("involved_characters"):
                raise ValueError("分析結果の転換点（該当なし）に関係人物が含まれています。")
            reason = point.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("分析結果の転換点（該当なし）に理由がありません。")
        else:
            raise ValueError("分析結果の転換点のavailabilityが不正です。")
    characters = analysis.characters
    names = [character.get("name") for character in characters]
    arc_characters = analysis.emotion_arc.get("characters")
    if len(names) != len(set(names)) or not isinstance(arc_characters, dict) or set(names) != set(arc_characters):
        raise ValueError("分析結果のキャラクター集合が不正です。")
    arcs = [
        analysis.emotion_arc.get("valence"),
        analysis.emotion_arc.get("tension"),
        *arc_characters.values(),
    ]
    expected_arc_length = min(scene_count, max_points)
    if any(not isinstance(values, list) or len(values) != expected_arc_length for values in arcs):
        raise ValueError("分析結果の感情アーク長が不正です。")
    if analysis.emotion_arc.get("scene_mapping") != scene_mapping_payload(scene_count, max_points):
        raise ValueError("分析結果のscene mappingが現在のシーン数と一致しません。")
    valence = analysis.emotion_arc["valence"]
    tension = analysis.emotion_arc["tension"]
    if any(not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 7 for value in valence):
        raise ValueError("分析結果のValenceが不正です。")
    if any(not isinstance(value, int) or isinstance(value, bool) or not -3 <= value <= 3 for value in tension):
        raise ValueError("分析結果のTensionが不正です。")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 7
        for arc in arc_characters.values()
        for value in arc
    ):
        raise ValueError("分析結果のキャラクターValenceが不正です。")


def _apply_import_draft_edits(
    source: dict[str, object], edits: ImportDraftSaveRequest, *, max_points: int
) -> dict[str, object]:
    """Rebuild server-derived Import draft fields from the currently edited client draft."""
    parsed = FountainParser.parse(edits.fountain_text)
    if not parsed.scenes:
        raise ValueError("Fountain本文にシーンがありません。")

    structured = deepcopy(source)
    metadata = edits.metadata.model_dump()
    metadata["title"] = edits.title.strip()
    analysis = edits.analysis.model_dump()
    analysis["turning_points"] = [
        {**point, "label": TURNING_POINT_LABELS[point["tp_number"]]} for point in analysis["turning_points"]
    ]
    emotion_arc = edits.emotion_arc.model_dump()

    structured["source_fountain"] = parsed.source_fountain
    structured["scenes"] = [scene.to_dict() for scene in parsed.scenes]
    structured["source"] = {
        **structured["source"],  # type: ignore[arg-type]
        "sha256": sha256(parsed.source_fountain.encode("utf-8")).hexdigest(),
    }
    structured["metadata"] = metadata
    structured["analysis"] = analysis
    structured["emotion_arc"] = emotion_arc
    structured["narrator"] = edits.narrator.model_dump()
    structured["voice_assignments"] = [assignment.model_dump() for assignment in edits.voice_assignments]
    validate_canonical_analysis(
        CanonicalAnalysis(
            metadata=metadata,
            emotion_arc=emotion_arc,
            characters=analysis["characters"],
            turning_points=analysis["turning_points"],
        ),
        parsed.scene_count(),
        max_points=max_points,
    )
    structured["emotion_arc"]["valence_vector"] = default_valence_vectorizer.vectorize(  # type: ignore[index]
        [float(value) for value in emotion_arc["valence"]]
    )
    return structured


def draft_response(draft: ImportDraft) -> ImportDraftResponse:
    structured = draft.structured
    metadata = structured["metadata"]  # type: ignore[assignment]
    narrator_data = structured.get("narrator")
    narrator = NarratorSchema(**narrator_data) if isinstance(narrator_data, dict) else NarratorSchema()
    voice_assignments_data = structured.get("voice_assignments")
    voice_assignments = (
        [VoiceAssignmentSchema(**va) for va in voice_assignments_data]
        if isinstance(voice_assignments_data, list)
        else []
    )
    return ImportDraftResponse(
        document_id=draft.document_id,
        expected_version=draft.expected_version,
        version_id=int(structured["version_id"]),
        title=str(metadata["title"]),
        source_fountain=str(structured["source_fountain"]),
        metadata=metadata,
        scenes=[SceneSchema(**scene) for scene in structured["scenes"]],  # type: ignore[arg-type]
        analysis=AnalysisSchema(**structured["analysis"]),
        emotion_arc=EmotionArcSchema(**structured["emotion_arc"]),  # type: ignore[arg-type]
        narrator=narrator,
        voice_assignments=voice_assignments,
    )


_JAPANESE_CHAR_PATTERN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def _is_japanese_content(text: str) -> bool:
    return bool(_JAPANESE_CHAR_PATTERN.search(text))


def compose_enriched_fountain(source_fountain: str, analysis: CanonicalAnalysis) -> str:
    """Render the title and canonical scene body without analysis section headings."""
    metadata = analysis.metadata
    title = str(metadata.get("title", "")).strip()
    if not title or not str(metadata.get("logline", "")).strip():
        raise ValueError("分析結果にtitleまたはloglineがありません。")
    _required_metadata_text(metadata, "theme_setting")
    return f"Title: {title}\n\n{_source_scene_body(source_fountain)}\n"


def _source_scene_body(source_fountain: str) -> str:
    parsed = FountainParser.parse(source_fountain)
    if not parsed.scenes:
        raise ValueError("分析対象のFountain本文にシーン本文がありません。")
    rendered: list[str] = []
    for scene in parsed.scenes:
        body = "\n".join(scene.text.splitlines()[1:]).strip()
        heading = f"{scene.heading} #{scene.scene_number}#"
        rendered.append(f"{heading}\n\n{body}" if body else heading)
    return "\n\n".join(rendered)


def _required_metadata_text(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"分析結果に{key}がありません。")
    return value.strip()
