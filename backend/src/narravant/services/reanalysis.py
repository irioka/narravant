"""Asynchronous, versioned emotional-arc reanalysis."""

from __future__ import annotations

import json
from typing import Any

from narravant.core.fountain import FountainParser
from narravant.core.valence_vector import default_valence_vectorizer
from narravant.db.database import DocumentRepository
from narravant.domain.tasks import TaskStatus
from narravant.services.ingestion import (
    DocumentAnalyzer,
    ImportDraft,
    ImportDraftStore,
    validate_canonical_analysis,
)
from narravant.services.tasks import TaskManager
from narravant.storage.gcs import ScriptStorageClient


class ReanalysisSnapshotConflictError(ValueError):
    """The source immutable version changed while Vertex was generating an arc draft."""


class ImportDraftUnavailableError(ValueError):
    """The process-local Import draft expired or was replaced before its reanalysis finished."""


class ImportDraftReanalysisService:
    """Generate an unsaved Emotional Arc for a process-local Import draft."""

    def __init__(
        self,
        tasks: TaskManager,
        drafts: ImportDraftStore,
        analyzer: DocumentAnalyzer,
        *,
        emotion_arc_max_points: int,
    ) -> None:
        self.tasks = tasks
        self.drafts = drafts
        self.analyzer = analyzer
        self.emotion_arc_max_points = emotion_arc_max_points

    def start(self, owner_user_id: str, draft: ImportDraft) -> str:
        version_id = int(draft.structured["version_id"])
        return self.tasks.create(
            owner_user_id,
            "emotion_arc_reanalysis",
            draft.document_id,
            {
                "document_id": draft.document_id,
                "version_id": version_id,
                "import_task_id": draft.task_id,
            },
        )

    def run(self, task_id: str, *, import_task_id: str, source_fountain: str) -> None:
        task = self.tasks.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        payload = json.loads(task["payload_json"])
        try:
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return

            self.tasks.start(task_id)
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "reading", "percentage": 10, "message": "Import draftを確認しています。"},
            )
            draft = self._get_active_draft(import_task_id, task["owner_user_id"], payload)
            if draft.structured.get("analysis", {}).get("status") != "completed":
                raise ValueError("分析済みでないImport draftは再分析できません。")

            parsed = FountainParser.parse(source_fountain)
            existing_character_names = [
                item["name"]
                for item in draft.structured.get("analysis", {}).get("characters", [])
                if isinstance(item, dict) and item.get("name")
            ]
            spoken_character_names = parsed.dialogue_character_names()
            expected_characters = None if spoken_character_names else existing_character_names or None
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "analyzing", "percentage": 60, "message": "Emotional Arcを再分析しています。"},
            )
            analysis = self.analyzer.analyze(
                source_fountain,
                lambda received_characters: self.tasks.publish(
                    task_id,
                    "progress",
                    {
                        "phase": "analyzing",
                        "percentage": 60,
                        "message": "生成AIからの応答を受信しています。",
                        "received_characters": received_characters,
                    },
                ),
                expected_characters=expected_characters,
            )
            validate_canonical_analysis(
                analysis,
                parsed.scene_count(),
                max_points=self.emotion_arc_max_points,
            )
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return

            active_draft = self._get_active_draft(import_task_id, task["owner_user_id"], payload)
            emotion_arc = {
                "valence": analysis.emotion_arc["valence"],
                "tension": analysis.emotion_arc["tension"],
                "characters": analysis.emotion_arc["characters"],
                "scene_mapping": analysis.emotion_arc["scene_mapping"],
                "valence_vector": default_valence_vectorizer.vectorize(analysis.emotion_arc["valence"]),
            }
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "structuring", "percentage": 90, "message": "再分析結果を準備しています。"},
            )
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return
            self.tasks.complete(
                task_id,
                {
                    "document_id": active_draft.document_id,
                    "version_id": int(active_draft.structured["version_id"]),
                    "reanalysis": {"emotion_arc": emotion_arc},
                },
            )
        except ImportDraftUnavailableError:
            self.tasks.fail(
                task_id,
                {
                    "code": "IMPORT_DRAFT_EXPIRED",
                    "message": "Import draftを確認できません。Importをやり直してください。",
                    "retryable": True,
                },
            )
        except Exception:
            self.tasks.fail(
                task_id,
                {"code": "REANALYSIS_FAILED", "message": "Valenceの再分析に失敗しました。", "retryable": True},
            )

    def _get_active_draft(self, import_task_id: str, owner_user_id: str, payload: dict[str, Any]) -> ImportDraft:
        draft = self.drafts.get(import_task_id)
        if (
            draft is None
            or draft.owner_user_id != owner_user_id
            or draft.document_id != payload["document_id"]
            or draft.task_id != payload["import_task_id"]
            or int(draft.structured["version_id"]) != int(payload["version_id"])
        ):
            raise ImportDraftUnavailableError("Import draft expired or changed during Emotional Arc reanalysis.")
        return draft

    def _cancel_requested(self, task_id: str) -> bool:
        task = self.tasks.repository.get(task_id)
        return task is not None and TaskStatus(task["status"]) is TaskStatus.CANCEL_REQUESTED


class ReanalysisService:
    """Generate a non-persistent emotional-arc draft for the current immutable version."""

    def __init__(
        self,
        repository: DocumentRepository,
        storage: ScriptStorageClient,
        tasks: TaskManager,
        analyzer: DocumentAnalyzer,
        *,
        emotion_arc_max_points: int,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.tasks = tasks
        self.analyzer = analyzer
        self.emotion_arc_max_points = emotion_arc_max_points

    def start(self, owner_user_id: str, document: dict[str, Any]) -> str:
        content_version_id = int(document["current_version_id"])
        document_lock_version = int(document["version_id"])
        version = self.repository.get_document_version(document["document_id"], content_version_id)
        if version is None:
            raise FileNotFoundError(document["document_id"])
        return self.tasks.create(
            owner_user_id,
            "emotion_arc_reanalysis",
            document["document_id"],
            {
                "document_id": document["document_id"],
                "base_content_version_id": content_version_id,
                "expected_document_lock_version": document_lock_version,
                "source_uri": version["gcs_uri"],
                "source_generation": int(version["gcs_generation"]),
                "source_payload_sha256": version["payload_sha256"],
            },
        )

    def run(self, task_id: str, *, source_fountain: str | None = None) -> None:
        task = self.tasks.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        payload = json.loads(task["payload_json"])
        document_id = payload["document_id"]
        base_content_version_id = int(payload["base_content_version_id"])
        expected_document_lock_version = int(payload["expected_document_lock_version"])
        try:
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return

            self.tasks.start(task_id)
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "reading", "percentage": 10, "message": "現在の脚本versionを検証しています。"},
            )
            source, source_metadata = self.storage.read_structured_script(document_id, base_content_version_id)
            if (
                source_metadata.uri != payload["source_uri"]
                or source_metadata.generation != payload["source_generation"]
            ):
                raise ReanalysisSnapshotConflictError("再分析対象の文書generationが変化しています。")
            if source_metadata.sha256 != payload["source_payload_sha256"]:
                raise ReanalysisSnapshotConflictError("再分析対象の文書内容が変化しています。")
            if source["analysis"]["status"] != "completed":
                raise ValueError("分析済みでない文書は再分析できません。")
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return

            existing_character_names = [
                item["name"]
                for item in source.get("analysis", {}).get("characters", [])
                if isinstance(item, dict) and item.get("name")
            ]
            analysis_source_fountain = source_fountain if source_fountain is not None else source["source_fountain"]
            parsed = FountainParser.parse(analysis_source_fountain)
            spoken_character_names = parsed.dialogue_character_names()
            expected_characters = None if spoken_character_names else existing_character_names or None
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "analyzing", "percentage": 60, "message": "Emotional Arcを再分析しています。"},
            )
            analysis = self.analyzer.analyze(
                analysis_source_fountain,
                lambda received_characters: self.tasks.publish(
                    task_id,
                    "progress",
                    {
                        "phase": "analyzing",
                        "percentage": 60,
                        "message": "生成AIからの応答を受信しています。",
                        "received_characters": received_characters,
                    },
                ),
                expected_characters=expected_characters,
            )
            scene_count = parsed.scene_count()
            validate_canonical_analysis(
                analysis,
                scene_count,
                max_points=self.emotion_arc_max_points,
            )
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return

            emotion_arc = {
                "valence": analysis.emotion_arc["valence"],
                "tension": analysis.emotion_arc["tension"],
                "characters": analysis.emotion_arc["characters"],
                "scene_mapping": analysis.emotion_arc["scene_mapping"],
                "valence_vector": default_valence_vectorizer.vectorize(analysis.emotion_arc["valence"]),
            }
            self._assert_snapshot_is_current(
                document_id,
                base_content_version_id,
                expected_document_lock_version,
                payload,
            )
            self.tasks.publish(
                task_id,
                "progress",
                {"phase": "structuring", "percentage": 90, "message": "再分析結果を準備しています。"},
            )
            if self._cancel_requested(task_id):
                self.tasks.cancel(task_id, "再分析をキャンセルしました。")
                return
            self.tasks.complete(
                task_id,
                {
                    "document_id": document_id,
                    "version_id": base_content_version_id,
                    "reanalysis": {"emotion_arc": emotion_arc},
                },
            )
        except ReanalysisSnapshotConflictError:
            self.tasks.fail(
                task_id,
                {
                    "code": "CONFLICT",
                    "message": "文書が更新されたため再分析結果を適用できませんでした。",
                    "retryable": True,
                },
            )
        except Exception:
            self.tasks.fail(
                task_id,
                {"code": "REANALYSIS_FAILED", "message": "Valenceの再分析に失敗しました。", "retryable": True},
            )

    def _cancel_requested(self, task_id: str) -> bool:
        task = self.tasks.repository.get(task_id)
        return task is not None and TaskStatus(task["status"]) is TaskStatus.CANCEL_REQUESTED

    def _assert_snapshot_is_current(
        self,
        document_id: str,
        base_content_version_id: int,
        expected_document_lock_version: int,
        snapshot: dict[str, Any],
    ) -> None:
        current = self.repository.get_document(document_id)
        version = self.repository.get_document_version(document_id, base_content_version_id)
        if current is None or version is None:
            raise ReanalysisSnapshotConflictError(document_id)
        if (
            int(current["current_version_id"]) != base_content_version_id
            or int(current["version_id"]) != expected_document_lock_version
            or version["gcs_uri"] != snapshot["source_uri"]
            or int(version["gcs_generation"]) != int(snapshot["source_generation"])
            or version["payload_sha256"] != snapshot["source_payload_sha256"]
        ):
            raise ReanalysisSnapshotConflictError(document_id)
        _, metadata = self.storage.read_structured_script(document_id, base_content_version_id)
        if (
            metadata.uri != snapshot["source_uri"]
            or metadata.generation != int(snapshot["source_generation"])
            or metadata.sha256 != snapshot["source_payload_sha256"]
        ):
            raise ReanalysisSnapshotConflictError(document_id)
