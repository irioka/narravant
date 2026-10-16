"""Owner-scoped SQLite repository for custom voice metadata."""

from __future__ import annotations

from collections.abc import Iterable

from narravant.db.database import DatabaseManager
from narravant.services.voice_models import StoredVoiceRecord, canonical_voice_id


class VoiceRegistryRepository:
    """Read and update voice metadata without storing scripts or credentials."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _require_owner(owner_user_id: str) -> str:
        owner = owner_user_id.strip()
        if not owner:
            raise ValueError("owner_user_id is required")
        return owner

    def list(self, owner_user_id: str) -> list[StoredVoiceRecord]:
        owner = self._require_owner(owner_user_id)
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM voice_registry WHERE owner_user_id = ? ORDER BY registered_at, voice_id",
                (owner,),
            ).fetchall()
        return [StoredVoiceRecord(**dict(row)) for row in rows]

    def get(self, owner_user_id: str, voice_id: str) -> StoredVoiceRecord | None:
        owner = self._require_owner(owner_user_id)
        canonical_id = canonical_voice_id(voice_id)
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM voice_registry WHERE owner_user_id = ? AND voice_id = ?",
                (owner, canonical_id),
            ).fetchone()
        return StoredVoiceRecord(**dict(row)) if row else None

    def ids_registered_for_other_owners(self, owner_user_id: str, voice_ids: Iterable[str]) -> set[str]:
        """Return only matching IDs held by another owner, without reading their metadata."""
        owner = self._require_owner(owner_user_id)
        canonical_ids = sorted({canonical_voice_id(voice_id) for voice_id in voice_ids})
        if not canonical_ids:
            return set()

        conflicts: set[str] = set()
        # Leave room for the owner parameter on SQLite builds with the usual
        # 999-variable limit. The query only checks IDs requested by this caller.
        with self.db.session() as conn:
            for start in range(0, len(canonical_ids), 500):
                batch = canonical_ids[start : start + 500]
                placeholders = ", ".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT DISTINCT voice_id FROM voice_registry "
                    f"WHERE owner_user_id <> ? AND voice_id IN ({placeholders})",
                    [owner, *batch],
                ).fetchall()
                conflicts.update(str(row["voice_id"]) for row in rows)
        return conflicts

    def upsert(self, record: StoredVoiceRecord) -> None:
        owner = self._require_owner(record.owner_user_id)
        voice_id = canonical_voice_id(record.voice_id)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO voice_registry (
                    owner_user_id, voice_id, speaker_label, voice_traits, language_code,
                    created_at, created_at_source, registered_at, expire_time,
                    last_checked_at, availability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, voice_id) DO UPDATE SET
                    speaker_label = COALESCE(excluded.speaker_label, voice_registry.speaker_label),
                    voice_traits = COALESCE(excluded.voice_traits, voice_registry.voice_traits),
                    language_code = COALESCE(excluded.language_code, voice_registry.language_code),
                    created_at = COALESCE(excluded.created_at, voice_registry.created_at),
                    created_at_source = CASE
                        WHEN excluded.created_at_source = 'local_response' THEN 'local_response'
                        ELSE voice_registry.created_at_source
                    END,
                    expire_time = excluded.expire_time,
                    last_checked_at = excluded.last_checked_at,
                    availability = excluded.availability
                """,
                (
                    owner,
                    voice_id,
                    record.speaker_label,
                    record.voice_traits,
                    record.language_code,
                    record.created_at,
                    record.created_at_source,
                    record.registered_at,
                    record.expire_time,
                    record.last_checked_at,
                    record.availability,
                ),
            )

    def delete(self, owner_user_id: str, voice_id: str) -> None:
        owner = self._require_owner(owner_user_id)
        canonical_id = canonical_voice_id(voice_id)
        with self.db.session() as conn:
            conn.execute(
                "DELETE FROM voice_registry WHERE owner_user_id = ? AND voice_id = ?",
                (owner, canonical_id),
            )
