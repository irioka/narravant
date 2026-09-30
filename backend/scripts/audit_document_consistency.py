"""Read-only SQLite/GCS consistency audit for canonical v1 document payloads."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from narravant.core.emotion_arc_resolution import scene_mapping_payload
from narravant.core.settings import Settings
from narravant.storage.local import LocalScriptStorageClient
from narravant.storage.protocol import ScriptStorageClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def validate_payload(data: dict[str, Any], document_id: str, version_id: int, *, max_points: int) -> str | None:
    if data.get("schema_version") != 1:
        return "schema_version is not 1"
    if data.get("document_id") != document_id:
        return "document_id does not match SQLite"
    if data.get("version_id") != version_id:
        return "version_id does not match SQLite"
    if not isinstance(data.get("owner_user_id"), str) or not data["owner_user_id"].strip():
        return "owner_user_id is missing or empty"
    source = data.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("filename"), str)
        or not source["filename"].strip()
        or not isinstance(source.get("media_type"), str)
        or not source["media_type"].strip()
        or not isinstance(source.get("sha256"), str)
    ):
        return "source is malformed"
    if not isinstance(data.get("source_fountain"), str) or not data["source_fountain"].strip():
        return "source_fountain is missing or empty"
    if source["sha256"] != sha256(data["source_fountain"].encode("utf-8")).hexdigest():
        return "source sha256 does not match source_fountain"
    if not isinstance(data.get("metadata"), dict) or not isinstance(data["metadata"].get("title"), str):
        return "metadata is malformed"
    analysis = data.get("analysis")
    if (
        not isinstance(analysis, dict)
        or analysis.get("status") not in {"completed", "not_requested"}
        or not isinstance(analysis.get("turning_points"), list)
        or not isinstance(analysis.get("characters"), list)
    ):
        return "analysis is malformed"
    emotion_arc = data.get("emotion_arc")
    if not isinstance(data.get("scenes"), list) or not isinstance(emotion_arc, dict):
        return "v1 scenes or emotion_arc is malformed"
    valence = emotion_arc.get("valence")
    tension = emotion_arc.get("tension")
    characters = emotion_arc.get("characters")
    scene_mapping = emotion_arc.get("scene_mapping")
    valence_vector = emotion_arc.get("valence_vector")
    if (
        not isinstance(valence, list)
        or not isinstance(tension, list)
        or not isinstance(characters, dict)
        or not isinstance(scene_mapping, list)
        or not isinstance(valence_vector, list)
    ):
        return "emotion_arc is malformed"
    if len(valence_vector) != 10:
        return "emotion_arc.valence_vector must contain 10 values"
    scene_count = len(data["scenes"])
    expected_arc_length = min(scene_count, max_points)
    if analysis["status"] == "completed" and (
        len(valence) != expected_arc_length
        or len(tension) != expected_arc_length
        or scene_mapping != scene_mapping_payload(scene_count, max_points)
        or any(not isinstance(values, list) or len(values) != expected_arc_length for values in characters.values())
    ):
        return "completed emotion_arc does not match scene mapping or vector dimensions"
    if analysis["status"] == "not_requested" and (valence or tension or characters or scene_mapping):
        return "not_requested analysis must not include emotion_arc values"
    return None


def audit(
    db_path: Path, storage: ScriptStorageClient, gcs_prefix: str, *, max_points: int
) -> list[tuple[str, int, str]]:
    """Return `(document_id, version_id, reason)` mismatches without exposing screenplay text."""
    mismatches: list[tuple[str, int, str]] = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT v.document_id, v.version_id, v.gcs_uri, v.gcs_generation, v.payload_sha256
            FROM document_versions v
            JOIN documents d ON d.document_id = v.document_id
            WHERE d.deleted_at IS NULL
            ORDER BY v.document_id, v.version_id
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        document_id = str(row["document_id"])
        version_id = int(row["version_id"])
        bucket_name = getattr(storage, "bucket_name", "local")
        expected_uri = f"gs://{bucket_name}/{gcs_prefix}{document_id}/v{version_id}.json"
        if row["gcs_uri"] != expected_uri and not row["gcs_uri"].startswith("file://"):
            mismatches.append((document_id, version_id, "gcs_uri does not match v1 layout"))
            continue
        try:
            payload, metadata = storage.read_structured_script(document_id, version_id)
        except FileNotFoundError:
            mismatches.append((document_id, version_id, "canonical GCS object is missing"))
            continue
        reason = validate_payload(payload, document_id, version_id, max_points=max_points)
        if reason:
            mismatches.append((document_id, version_id, reason))
        elif metadata.generation != int(row["gcs_generation"]):
            mismatches.append((document_id, version_id, "GCS generation does not match SQLite"))
        elif metadata.sha256 != row["payload_sha256"]:
            mismatches.append((document_id, version_id, "GCS payload hash does not match SQLite"))
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only v1 document consistency audit")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--gcs-prefix", default="scripts/")
    args = parser.parse_args()
    if not args.gcs_prefix.endswith("/"):
        parser.error("--gcs-prefix must end with '/'")
    if not args.db_path.is_file():
        parser.error("--db-path must point to an existing SQLite file")

    try:
        load_dotenv(BACKEND_ROOT.parent / ".env", override=False)
        settings = Settings.load(BACKEND_ROOT)
        storage_dir = getattr(settings, "local_storage_path", BACKEND_ROOT / "runtime" / "storage")
        mismatches = audit(
            args.db_path,
            LocalScriptStorageClient(storage_dir),
            args.gcs_prefix,
            max_points=settings.emotion_arc_max_points,
        )
    except Exception as exc:
        print(f"operational failure: {type(exc).__name__}", file=sys.stderr)
        return 1

    if mismatches:
        for document_id, version_id, reason in mismatches:
            print(f"mismatch document_id={document_id} version_id={version_id} reason={reason}")
        return 2
    print("consistent document_versions=all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
