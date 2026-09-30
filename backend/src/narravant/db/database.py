"""SQLite database management with WAL mode, optimistic locking, and 10D Valence search."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import sqlite_vec


class OptimisticLockError(Exception):
    """Raised when an update fails due to a version conflict (lost update prevention)."""

    def __init__(self, document_id: str, expected_version: int, actual_version: int | None = None) -> None:
        msg = (
            f"Optimistic lock conflict on document {document_id}: "
            f"expected version {expected_version}, but was {actual_version}"
        )
        super().__init__(msg)
        self.document_id = document_id
        self.expected_version = expected_version
        self.actual_version = actual_version


DocumentSortField = Literal["title", "owner_email", "shared_count", "updated_at", "version_id", "arc_distance"]
SortDirection = Literal["asc", "desc"]
DOCUMENT_LIST_COLUMNS = """
    d.document_id,
    d.owner_user_id,
    d.title,
    d.current_version_id,
    d.current_version_id AS version_id,
    d.version_id AS expected_version,
    d.created_at,
    d.updated_at,
    0 AS shared_count
"""


@dataclass(frozen=True)
class DocumentSort:
    """Allowed list sort column and direction. SQL identifiers are derived only from this type."""

    field: DocumentSortField
    direction: SortDirection


SCHEMA_SQL = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Documents metadata table
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    current_version_id INTEGER NOT NULL DEFAULT 1,
    version_id INTEGER NOT NULL DEFAULT 1,
    search_generation INTEGER,
    search_index_status TEXT NOT NULL DEFAULT 'not_indexed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_at);

CREATE TABLE IF NOT EXISTS background_tasks (
    task_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id),
    task_type TEXT NOT NULL,
    document_id TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_background_tasks_owner_status_updated
    ON background_tasks(owner_user_id, status, updated_at);

CREATE TABLE IF NOT EXISTS task_events (
    task_id TEXT NOT NULL REFERENCES background_tasks(task_id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_event ON task_events(task_id, event_id);

-- Document versions history table
CREATE TABLE IF NOT EXISTS document_versions (
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL,
    gcs_uri TEXT NOT NULL,
    gcs_generation INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    is_saved INTEGER NOT NULL CHECK (is_saved IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, version_id)
);

-- sqlite-vec virtual table for 10-dimensional Valence cosine search
CREATE VIRTUAL TABLE IF NOT EXISTS valence_vectors USING vec0(
    valence_vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT,
    version_id INTEGER,
    valence_vector float[10] distance_metric=cosine
);
"""


class DatabaseManager:
    """Manages SQLite connections with WAL, busy_timeout, and sqlite-vec extension."""

    def __init__(self, db_path: str = "runtime/narravant.sqlite3") -> None:
        self.db_path = db_path
        self._memory_conn: sqlite3.Connection | None = None
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            # Keep a persistent connection open so in-memory database persists across sessions
            self._memory_conn = self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Required architecture pragmas
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # Load sqlite-vec extension
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        return conn

    def create_connection(self) -> sqlite3.Connection:
        """Create and configure a connection with sqlite-vec and proper pragmas."""
        return self._open_connection()

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for acquiring a managed connection and handling transactions."""
        conn = self.create_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self.db_path != ":memory:":
                conn.close()

    @contextmanager
    def immediate_session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for acquiring a connection serialized by BEGIN IMMEDIATE."""
        conn = self.create_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def init_schema(self) -> None:
        """Initialize the single current development schema, rejecting stale SQLite files."""
        with self.session() as conn:
            conn.executescript(SCHEMA_SQL)
            user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            required_user_columns = {"user_id", "email", "display_name", "created_at", "updated_at"}
            unique_user_indexes = {
                tuple(column["name"] for column in conn.execute(f"PRAGMA index_info('{index['name']}')"))
                for index in conn.execute("PRAGMA index_list(users)")
                if index["unique"]
            }
            if (
                not required_user_columns <= user_columns
                or "password_hash" in user_columns
                or ("email",) not in unique_user_indexes
            ):
                raise RuntimeError(
                    "SQLite users schema is stale; recreate development data from the current v1 schema."
                )
            version_columns = {row["name"] for row in conn.execute("PRAGMA table_info(document_versions)")}
            if "is_saved" not in version_columns:
                raise RuntimeError(
                    "SQLite document_versions schema is stale; recreate development data from the current v1 schema."
                )
            doc_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
            if (
                not {"document_id", "owner_user_id", "title", "current_version_id", "version_id"} <= doc_columns
                or "is_public" in doc_columns
                or "sharing_version" in doc_columns
            ):
                raise RuntimeError(
                    "SQLite documents schema is stale; recreate development data from the current v1 schema."
                )
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "document_shares" in tables:
                raise RuntimeError("SQLite schema is stale; document_shares must not exist in the current v1 schema.")
            vector_table = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'valence_vectors'"
            ).fetchone()
            vector_sql = str(vector_table["sql"] or "").replace(" ", "").lower() if vector_table else ""
            if "valence_vectorfloat[10]" not in vector_sql:
                raise RuntimeError(
                    "SQLite valence_vectors schema is stale; recreate development data from the current v1 schema."
                )


def now_utc_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def _serialize_valence_vector(vector: Sequence[float]) -> bytes:
    """Validate the current 10-dimensional unit-vector contract before sqlite-vec storage."""
    if len(vector) != 10:
        raise ValueError("valence_vector must have 10 dimensions")
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as exc:
        raise ValueError("valence_vector must contain only finite values") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError("valence_vector must contain only finite values")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("valence_vector must be a unit vector")
    return sqlite_vec.serialize_float32(values)


DOCUMENT_SORT_COLUMNS: dict[DocumentSortField, str] = {
    "title": "d.title",
    "owner_email": "u.email",
    "shared_count": "shared_count",
    "updated_at": "d.updated_at",
    "version_id": "d.version_id",
    "arc_distance": "matches.arc_distance",
}
DEFAULT_DOCUMENT_SORTS: tuple[DocumentSort, ...] = (DocumentSort("updated_at", "desc"),)
DEFAULT_SIMILARITY_SORTS: tuple[DocumentSort, ...] = (DocumentSort("arc_distance", "asc"),)
DOCUMENT_ID_TIE_BREAKER = "d.document_id ASC"
DOCUMENT_VISIBILITY_SQL = "d.owner_user_id = ?"


class DocumentRepository:
    """Handles CRUD, optimistic locking, and similarity searches."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def ensure_user(self, user_id: str, email: str, display_name: str) -> None:
        """Ensure default/seed user exists in database."""
        now = now_utc_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, email, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (user_id, email, display_name, now, now),
            )

    def create_document(
        self,
        document_id: str,
        owner_user_id: str,
        title: str,
        gcs_uri: str,
        gcs_generation: int,
        payload_sha256: str,
        valence_vector: list[float] | None = None,
        initial_is_saved: bool = True,
    ) -> dict[str, Any]:
        """Insert new document, initial version (v1), and Valence vector index."""
        now = now_utc_iso()
        with self.db.session() as conn:
            # 1. Insert documents row
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, owner_user_id, title,
                    current_version_id, version_id, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 1, ?, ?)
                """,
                (document_id, owner_user_id, title, now, now),
            )

            # 2. Insert document_versions row
            conn.execute(
                """
                INSERT INTO document_versions (
                    document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (document_id, gcs_uri, gcs_generation, payload_sha256, title, int(initial_is_saved), now),
            )

            # 3. Insert the current 10D Valence vector if provided.
            if valence_vector is not None:
                serialized = _serialize_valence_vector(valence_vector)
                conn.execute(
                    """
                    INSERT INTO valence_vectors (document_id, version_id, valence_vector)
                    VALUES (?, 1, ?)
                    """,
                    (document_id, serialized),
                )

        return self.get_document(document_id)  # type: ignore[return-value]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        """Fetch a single document metadata."""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT d.*, d.version_id AS expected_version, v.gcs_uri, v.is_saved, u.email AS owner_email,
                       0 AS shared_count
                FROM documents d
                JOIN users u ON u.user_id = d.owner_user_id
                LEFT JOIN document_versions v
                    ON d.document_id = v.document_id AND d.current_version_id = v.version_id
                WHERE d.document_id = ? AND d.deleted_at IS NULL
                """,
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_document_versions(self, document_id: str) -> tuple[int, list[dict[str, Any]]] | None:
        """Return immutable history metadata without reading canonical payload bodies."""
        with self.db.session() as conn:
            document = conn.execute(
                """
                SELECT current_version_id
                FROM documents
                WHERE document_id = ? AND deleted_at IS NULL
                """,
                (document_id,),
            ).fetchone()
            if document is None:
                return None
            current_version_id = int(document["current_version_id"])
            rows = conn.execute(
                """
                SELECT version_id, title, created_at, payload_sha256
                FROM document_versions
                WHERE document_id = ? AND is_saved = 1
                ORDER BY version_id DESC
                """,
                (document_id,),
            ).fetchall()
        return current_version_id, [dict(row) for row in rows]

    def get_document_version(self, document_id: str, version_id: int) -> dict[str, Any] | None:
        """Return metadata for one visible immutable version."""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT v.*, d.current_version_id
                FROM document_versions v
                JOIN documents d ON d.document_id = v.document_id
                WHERE v.document_id = ? AND v.version_id = ? AND d.deleted_at IS NULL
                """,
                (document_id, version_id),
            ).fetchone()
            return dict(row) if row else None

    def has_unpublished_draft_version(self, document_id: str) -> bool:
        """Return whether a document already owns a replacement draft awaiting Save or discard."""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT 1 FROM document_versions WHERE document_id = ? AND is_saved = 0 LIMIT 1",
                (document_id,),
            ).fetchone()
        return row is not None

    def list_documents(
        self,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        similar_to_vector: list[float] | None = None,
        max_arc_distance: float | None = None,
        sorts: Sequence[DocumentSort] | None = None,
        viewer_user_id: str | None = None,
        viewer_email: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List documents with SQL filtering, stable multi-sort, and optional arc similarity."""
        with self.db.session() as conn:
            where_clauses = ["d.deleted_at IS NULL"]
            params: list[Any] = []
            if viewer_user_id is not None:
                where_clauses.append(DOCUMENT_VISIBILITY_SQL)
                params.append(viewer_user_id)
            if query:
                where_clauses.append("d.title LIKE ?")
                params.append(f"%{query}%")
            where_sql = " AND ".join(where_clauses)

            if similar_to_vector is not None:
                eligible_count_row = conn.execute(
                    f"SELECT COUNT(*) FROM documents d WHERE {where_sql}", params
                ).fetchone()
                eligible_count = eligible_count_row[0] if eligible_count_row else 0
                if eligible_count == 0:
                    return [], 0

                vector_count_row = conn.execute("SELECT COUNT(*) FROM valence_vectors").fetchone()
                vector_count = vector_count_row[0] if vector_count_row else 0
                if vector_count == 0:
                    return [], 0

                serialized = _serialize_valence_vector(similar_to_vector)
                cte_sql = """
                WITH matches AS (
                    SELECT document_id, version_id, distance AS arc_distance
                    FROM valence_vectors
                    WHERE valence_vector MATCH ? AND k = ?
                )
                """
                dist_clause = " AND matches.arc_distance <= ?" if max_arc_distance is not None else ""
                dist_params = [max_arc_distance] if max_arc_distance is not None else []
                total_row = conn.execute(
                    f"""
                    {cte_sql}
                    SELECT COUNT(*)
                    FROM matches
                    JOIN documents d
                        ON d.document_id = matches.document_id
                        AND d.current_version_id = matches.version_id
                    WHERE {where_sql}{dist_clause}
                    """,
                    [serialized, vector_count, *params, *dist_params],
                ).fetchone()
                total = total_row[0] if total_row else 0
                order_sql = self._order_by_sql(sorts or DEFAULT_SIMILARITY_SORTS, include_distance=True)
                select_sql = f"""
                {cte_sql}
                SELECT {DOCUMENT_LIST_COLUMNS}, v.gcs_uri, v.is_saved, u.email AS owner_email,
                       matches.arc_distance
                FROM matches
                JOIN documents d
                    ON d.document_id = matches.document_id
                    AND d.current_version_id = matches.version_id
                JOIN users u ON u.user_id = d.owner_user_id
                LEFT JOIN document_versions v
                    ON d.document_id = v.document_id AND d.current_version_id = v.version_id
                WHERE {where_sql}{dist_clause}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """
                rows = conn.execute(
                    select_sql, [serialized, vector_count, *params, *dist_params, limit, offset]
                ).fetchall()
                return [dict(r) for r in rows], total

            count_row = conn.execute(f"SELECT COUNT(*) FROM documents d WHERE {where_sql}", params).fetchone()
            total = count_row[0] if count_row else 0
            order_sql = self._order_by_sql(sorts or DEFAULT_DOCUMENT_SORTS, include_distance=False)
            select_sql = f"""
            SELECT {DOCUMENT_LIST_COLUMNS}, v.gcs_uri, v.is_saved, u.email AS owner_email
            FROM documents d
            JOIN users u ON u.user_id = d.owner_user_id
            LEFT JOIN document_versions v
                ON d.document_id = v.document_id AND d.current_version_id = v.version_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """
            rows = conn.execute(select_sql, [*params, limit, offset]).fetchall()
            return [dict(r) for r in rows], total

    def list_current_document_versions(self) -> list[tuple[str, int]]:
        """Return saved current revisions for local Valence-index reconstruction."""
        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT d.document_id, d.current_version_id
                FROM documents d
                JOIN document_versions v
                    ON v.document_id = d.document_id
                    AND v.version_id = d.current_version_id
                WHERE d.deleted_at IS NULL AND v.is_saved = 1
                """
            ).fetchall()
        return [(str(row["document_id"]), int(row["current_version_id"])) for row in rows]

    def replace_current_valence_vectors(self, entries: Sequence[tuple[str, int, Sequence[float]]]) -> None:
        """Atomically replace the derived current-version index from local canonical files."""
        with self.db.session() as conn:
            conn.execute("DELETE FROM valence_vectors")
            for document_id, version_id, vector in entries:
                conn.execute(
                    "INSERT INTO valence_vectors (document_id, version_id, valence_vector) VALUES (?, ?, ?)",
                    (document_id, version_id, _serialize_valence_vector(vector)),
                )

    @staticmethod
    def _order_by_sql(sorts: Sequence[DocumentSort], *, include_distance: bool) -> str:
        """Build ORDER BY only from the fixed field allow-list, then add a stable tie-breaker."""
        clauses: list[str] = []
        for sort in sorts:
            if sort.field == "arc_distance" and not include_distance:
                raise ValueError("arc_distance sorting requires a similarity query")
            column = DOCUMENT_SORT_COLUMNS.get(sort.field)
            if column is None:
                raise ValueError(f"Unsupported document sort field: {sort.field}")
            if sort.direction not in {"asc", "desc"}:
                raise ValueError(f"Unsupported document sort direction: {sort.direction}")
            clauses.append(f"{column} {sort.direction.upper()}")
        clauses.append(DOCUMENT_ID_TIE_BREAKER)
        return ", ".join(clauses)

    def update_document(
        self,
        document_id: str,
        expected_version: int,
        title: str | None = None,
        new_gcs_uri: str | None = None,
        new_gcs_generation: int | None = None,
        new_payload_sha256: str | None = None,
        new_valence_vector: list[float] | None = None,
        new_content_version_id: int | None = None,
    ) -> dict[str, Any]:
        """Update document with optimistic locking check.

        Increments version_id and current_version_id if new_gcs_uri is provided.
        """
        now = now_utc_iso()
        with self.db.session() as conn:
            # Check current version
            current = conn.execute(
                """
                SELECT version_id, current_version_id, title
                FROM documents
                WHERE document_id = ? AND deleted_at IS NULL
                """,
                (document_id,),
            ).fetchone()

            if not current:
                raise FileNotFoundError(f"Document not found: {document_id}")

            current_ver = current["version_id"]
            if current_ver != expected_version:
                raise OptimisticLockError(
                    document_id=document_id,
                    expected_version=expected_version,
                    actual_version=current_ver,
                )

            next_ver_counter = current_ver + 1
            next_content_ver = current["current_version_id"]

            if new_gcs_uri:
                if new_gcs_generation is None or new_payload_sha256 is None or new_content_version_id is None:
                    raise ValueError("New document version requires GCS generation, payload hash, and content version")
                maximum_row = conn.execute(
                    "SELECT COALESCE(MAX(version_id), 0) AS maximum FROM document_versions WHERE document_id = ?",
                    (document_id,),
                ).fetchone()
                expected_content_version = int(maximum_row["maximum"]) + 1
                if new_content_version_id != expected_content_version:
                    raise ValueError(
                        f"New content version must be {expected_content_version}, got {new_content_version_id}"
                    )
                next_content_ver = new_content_version_id
                # Insert version record
                conn.execute(
                    """
                    INSERT INTO document_versions (
                        document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        document_id,
                        next_content_ver,
                        new_gcs_uri,
                        new_gcs_generation,
                        new_payload_sha256,
                        title if title is not None else current["title"],
                        now,
                    ),
                )

                if new_valence_vector is not None:
                    serialized = _serialize_valence_vector(new_valence_vector)
                    conn.execute(
                        """
                        INSERT INTO valence_vectors (document_id, version_id, valence_vector)
                        VALUES (?, ?, ?)
                        """,
                        (document_id, next_content_ver, serialized),
                    )

            # Update document record with optimistic lock condition
            set_parts = [
                "version_id = ?",
                "current_version_id = ?",
                "updated_at = ?",
            ]
            params: list[Any] = [next_ver_counter, next_content_ver, now]

            if title is not None:
                set_parts.append("title = ?")
                params.append(title)

            params.extend([document_id, expected_version])

            cursor = conn.execute(
                f"""
                UPDATE documents
                SET {", ".join(set_parts)}
                WHERE document_id = ? AND version_id = ?
                """,
                params,
            )

            if cursor.rowcount == 0:
                raise OptimisticLockError(
                    document_id=document_id,
                    expected_version=expected_version,
                )

        return self.get_document(document_id)  # type: ignore[return-value]

    def rename_document(
        self,
        document_id: str,
        expected_version: int,
        title: str,
    ) -> dict[str, Any]:
        """Rename a document without creating a new content version.

        Updates documents.title and the CURRENT content version's title in
        document_versions, advancing only the optimistic-lock counter
        (version_id). The content version (current_version_id) is unchanged.
        """
        now = now_utc_iso()
        with self.db.session() as conn:
            current = conn.execute(
                """
                SELECT version_id, current_version_id
                FROM documents
                WHERE document_id = ? AND deleted_at IS NULL
                """,
                (document_id,),
            ).fetchone()
            if not current:
                raise FileNotFoundError(f"Document not found: {document_id}")
            if int(current["version_id"]) != expected_version:
                raise OptimisticLockError(
                    document_id=document_id,
                    expected_version=expected_version,
                    actual_version=int(current["version_id"]),
                )
            content_ver = int(current["current_version_id"])
            conn.execute(
                "UPDATE document_versions SET title = ? WHERE document_id = ? AND version_id = ?",
                (title, document_id, content_ver),
            )
            cursor = conn.execute(
                """
                UPDATE documents
                SET title = ?, version_id = ?, updated_at = ?
                WHERE document_id = ? AND version_id = ?
                """,
                (title, expected_version + 1, now, document_id, expected_version),
            )
            if cursor.rowcount == 0:
                raise OptimisticLockError(document_id=document_id, expected_version=expected_version)
        return self.get_document(document_id)  # type: ignore[return-value]

    def create_unpublished_draft_version(
        self,
        document_id: str,
        expected_version: int,
        version_id: int,
        gcs_uri: str,
        gcs_generation: int,
        payload_sha256: str,
        title: str,
    ) -> None:
        """Record an immutable replacement payload without changing the published current version."""
        now = now_utc_iso()
        with self.db.session() as conn:
            current = conn.execute(
                "SELECT version_id FROM documents WHERE document_id = ? AND deleted_at IS NULL", (document_id,)
            ).fetchone()
            if current is None:
                raise FileNotFoundError(f"Document not found: {document_id}")
            if int(current["version_id"]) != expected_version:
                raise OptimisticLockError(document_id, expected_version, int(current["version_id"]))
            unsaved = conn.execute(
                "SELECT version_id FROM document_versions WHERE document_id = ? AND is_saved = 0", (document_id,)
            ).fetchone()
            if unsaved is not None:
                raise ValueError(f"Document has unpublished draft version {unsaved['version_id']}")
            maximum = conn.execute(
                "SELECT COALESCE(MAX(version_id), 0) AS maximum FROM document_versions WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if version_id != int(maximum["maximum"]) + 1:
                raise ValueError(f"New content version must be {int(maximum['maximum']) + 1}, got {version_id}")
            conn.execute(
                """
                INSERT INTO document_versions (
                    document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, is_saved, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (document_id, version_id, gcs_uri, gcs_generation, payload_sha256, title, now),
            )

    def finalize_draft_version(
        self,
        document_id: str,
        expected_version: int,
        version_id: int,
        title: str,
        gcs_uri: str,
        gcs_generation: int,
        payload_sha256: str,
        valence_vector: list[float],
    ) -> dict[str, Any]:
        """Publish an existing draft version and make it the sole current document version."""
        now = now_utc_iso()
        with self.db.session() as conn:
            current = conn.execute(
                "SELECT version_id FROM documents WHERE document_id = ? AND deleted_at IS NULL", (document_id,)
            ).fetchone()
            if current is None:
                raise FileNotFoundError(f"Document not found: {document_id}")
            if int(current["version_id"]) != expected_version:
                raise OptimisticLockError(document_id, expected_version, int(current["version_id"]))
            draft = conn.execute(
                "SELECT is_saved FROM document_versions WHERE document_id = ? AND version_id = ?",
                (document_id, version_id),
            ).fetchone()
            if draft is None:
                raise FileNotFoundError(f"Document draft not found: {document_id}/{version_id}")
            if bool(draft["is_saved"]):
                raise ValueError(f"Document version is already saved: {document_id}/{version_id}")
            conn.execute(
                """
                UPDATE document_versions
                SET gcs_uri = ?, gcs_generation = ?, payload_sha256 = ?, title = ?, is_saved = 1
                WHERE document_id = ? AND version_id = ? AND is_saved = 0
                """,
                (gcs_uri, gcs_generation, payload_sha256, title, document_id, version_id),
            )
            conn.execute(
                "DELETE FROM valence_vectors WHERE document_id = ? AND version_id = ?",
                (document_id, version_id),
            )
            conn.execute(
                "INSERT INTO valence_vectors (document_id, version_id, valence_vector) VALUES (?, ?, ?)",
                (document_id, version_id, _serialize_valence_vector(valence_vector)),
            )
            cursor = conn.execute(
                """
                UPDATE documents
                SET current_version_id = ?, title = ?, version_id = version_id + 1, updated_at = ?
                WHERE document_id = ? AND version_id = ? AND deleted_at IS NULL
                """,
                (version_id, title, now, document_id, expected_version),
            )
            if cursor.rowcount == 0:
                raise OptimisticLockError(document_id, expected_version)
        return self.get_document(document_id)  # type: ignore[return-value]

    def delete_unpublished_draft_version(self, document_id: str, expected_version: int, version_id: int) -> None:
        """Remove a discarded non-current draft metadata row after its GCS object was deleted."""
        with self.db.session() as conn:
            current = conn.execute(
                "SELECT version_id, current_version_id FROM documents WHERE document_id = ? AND deleted_at IS NULL",
                (document_id,),
            ).fetchone()
            if current is None:
                raise FileNotFoundError(f"Document not found: {document_id}")
            if int(current["version_id"]) != expected_version:
                raise OptimisticLockError(document_id, expected_version, int(current["version_id"]))
            if int(current["current_version_id"]) == version_id:
                raise ValueError("Current document version cannot be discarded")
            cursor = conn.execute(
                "DELETE FROM document_versions WHERE document_id = ? AND version_id = ? AND is_saved = 0",
                (document_id, version_id),
            )
            if cursor.rowcount == 0:
                raise FileNotFoundError(f"Unpublished document draft not found: {document_id}/{version_id}")

    def finalize_initial_draft(
        self,
        document_id: str,
        expected_version: int,
        title: str,
        gcs_uri: str,
        gcs_generation: int,
        payload_sha256: str,
        valence_vector: list[float],
    ) -> dict[str, Any]:
        """Confirm the upload-created v1 draft without creating a second history version."""
        now = now_utc_iso()
        with self.db.session() as conn:
            current = conn.execute(
                "SELECT version_id, current_version_id FROM documents WHERE document_id = ? AND deleted_at IS NULL",
                (document_id,),
            ).fetchone()
            if current is None:
                raise FileNotFoundError(f"Document not found: {document_id}")
            if int(current["version_id"]) != expected_version:
                raise OptimisticLockError(document_id, expected_version, int(current["version_id"]))
            if int(current["current_version_id"]) != 1:
                raise ValueError("Only the initial document draft can be finalized")
            version = conn.execute(
                "SELECT is_saved FROM document_versions WHERE document_id = ? AND version_id = 1",
                (document_id,),
            ).fetchone()
            if version is None or bool(version["is_saved"]):
                raise ValueError("Initial document draft is already saved")
            conn.execute(
                """
                UPDATE document_versions
                SET gcs_uri = ?, gcs_generation = ?, payload_sha256 = ?, title = ?, is_saved = 1
                WHERE document_id = ? AND version_id = 1 AND is_saved = 0
                """,
                (gcs_uri, gcs_generation, payload_sha256, title, document_id),
            )
            serialized = _serialize_valence_vector(valence_vector)
            conn.execute("DELETE FROM valence_vectors WHERE document_id = ? AND version_id = 1", (document_id,))
            conn.execute(
                "INSERT INTO valence_vectors (document_id, version_id, valence_vector) VALUES (?, 1, ?)",
                (document_id, serialized),
            )
            conn.execute(
                """
                UPDATE documents SET title = ?, version_id = ?, updated_at = ?
                WHERE document_id = ? AND version_id = ?
                """,
                (title, expected_version + 1, now, document_id, expected_version),
            )
        return self.get_document(document_id)  # type: ignore[return-value]

    def activate_existing_version(
        self, document_id: str, expected_version: int, target_version_id: int
    ) -> dict[str, Any]:
        """Make an existing saved immutable version current without emitting another version."""
        now = now_utc_iso()
        with self.db.session() as conn:
            target = conn.execute(
                """
                SELECT title FROM document_versions
                WHERE document_id = ? AND version_id = ? AND is_saved = 1
                """,
                (document_id, target_version_id),
            ).fetchone()
            if target is None:
                raise FileNotFoundError(f"Document version not found: {document_id}/{target_version_id}")
            cursor = conn.execute(
                """
                UPDATE documents
                SET current_version_id = ?, title = ?, version_id = version_id + 1, updated_at = ?
                WHERE document_id = ? AND version_id = ? AND deleted_at IS NULL
                """,
                (target_version_id, target["title"], now, document_id, expected_version),
            )
            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT version_id FROM documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                raise OptimisticLockError(
                    document_id, expected_version, int(current["version_id"]) if current else None
                )
        return self.get_document(document_id)  # type: ignore[return-value]

    def next_content_version_id(self, document_id: str) -> int:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_id), 0) AS maximum FROM document_versions WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        return int(row["maximum"]) + 1

    def soft_delete_document(self, document_id: str) -> bool:
        """Mark document as deleted."""
        now = now_utc_iso()
        with self.db.session() as conn:
            cursor = conn.execute(
                "UPDATE documents SET deleted_at = ?, updated_at = ? WHERE document_id = ? AND deleted_at IS NULL",
                (now, now, document_id),
            )
        return cursor.rowcount > 0


class TaskRepository:
    """SQLite persistence for task state and strictly monotonic task events."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create(
        self,
        task_id: str,
        owner_user_id: str,
        task_type: str,
        document_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        now = now_utc_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO background_tasks (
                    task_id, owner_user_id, task_type, document_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (task_id, owner_user_id, task_type, document_id, json.dumps(payload), now, now),
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM background_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def transition(self, task_id: str, expected_status: str, status: str) -> bool:
        with self.db.session() as conn:
            cursor = conn.execute(
                "UPDATE background_tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                (status, now_utc_iso(), task_id, expected_status),
            )
            return cursor.rowcount == 1

    def append_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> int:
        with self.db.session() as conn:
            next_id = int(
                conn.execute(
                    "SELECT COALESCE(MAX(event_id), 0) + 1 FROM task_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO task_events (task_id, event_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, next_id, event_type, json.dumps(payload), now_utc_iso()),
            )
        return next_id

    def events_after(self, task_id: str, event_id: int) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM task_events WHERE task_id = ? AND event_id > ? ORDER BY event_id", (task_id, event_id)
            ).fetchall()
        return [dict(row) for row in rows]

    def fail_interrupted(self) -> list[str]:
        """Mark work that cannot resume after a process restart as failed."""
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT task_id FROM background_tasks WHERE status IN ('queued', 'running', 'cancel_requested')"
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            conn.execute(
                """
                UPDATE background_tasks
                SET status = 'failed', updated_at = ?
                WHERE status IN ('queued', 'running', 'cancel_requested')
                """,
                (now_utc_iso(),),
            )
        return task_ids
