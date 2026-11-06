"""Owner-scoped SQLite name claims, shared across connections and workers."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import nullcontext
from uuid import uuid4

from narravant.db.database import DatabaseManager
from narravant.services.voice_models import canonical_voice_id, utc_iso
from narravant.services.voice_naming import (
    VoiceNameReservation,
    choose_available_name,
    normalize_name_key,
)


class VoiceNameRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def reserve(self, owner_user_id: str, base_name: str, observed_names: list[str]) -> VoiceNameReservation:
        if not owner_user_id.strip():
            raise ValueError("Owner is required")
        with self.db.immediate_session() as conn:
            occupied = {normalize_name_key(name) for name in observed_names}
            occupied.update(
                row[0]
                for row in conn.execute(
                    "SELECT name_key FROM voice_name_claims WHERE owner_user_id = ?", (owner_user_id,)
                )
            )
            occupied.update(
                normalize_name_key(row[0])
                for row in conn.execute(
                    "SELECT display_name FROM voice_registry "
                    "WHERE owner_user_id = ? AND availability = 'available' AND display_name IS NOT NULL",
                    (owner_user_id,),
                )
                if row[0].strip()
            )
            name = choose_available_name(base_name, occupied)
            reservation = VoiceNameReservation(str(uuid4()), owner_user_id, name, normalize_name_key(name))
            conn.execute(
                "INSERT INTO voice_name_claims (owner_user_id, name_key, display_name, attempt_id, state, created_at) "
                "VALUES (?, ?, ?, ?, 'reserved', ?)",
                (owner_user_id, reservation.name_key, name, reservation.attempt_id, utc_iso()),
            )
        return reservation

    def confirm(
        self, reservation: VoiceNameReservation, voice_id: str, *, connection: sqlite3.Connection | None = None
    ) -> None:
        with nullcontext(connection) if connection is not None else self.db.session() as conn:
            updated = conn.execute(
                "UPDATE voice_name_claims SET voice_id = ?, state = 'confirmed' "
                "WHERE owner_user_id = ? AND attempt_id = ? AND name_key = ?",
                (canonical_voice_id(voice_id), reservation.owner_user_id, reservation.attempt_id, reservation.name_key),
            )
            if updated.rowcount != 1:
                raise ValueError("Voice name claim is missing")

    def release_rejected(self, reservation: VoiceNameReservation) -> None:
        with self.db.session() as conn:
            conn.execute(
                "DELETE FROM voice_name_claims WHERE owner_user_id = ? AND attempt_id = ? AND state != 'confirmed'",
                (reservation.owner_user_id, reservation.attempt_id),
            )

    def mark_unknown(self, reservation: VoiceNameReservation) -> None:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE voice_name_claims SET state = 'unknown' "
                "WHERE owner_user_id = ? AND attempt_id = ? AND state = 'reserved'",
                (reservation.owner_user_id, reservation.attempt_id),
            )

    def release_confirmed(
        self, owner_user_id: str, voice_id: str, *, connection: sqlite3.Connection | None = None
    ) -> None:
        with nullcontext(connection) if connection is not None else self.db.session() as conn:
            conn.execute(
                "DELETE FROM voice_name_claims WHERE owner_user_id = ? AND voice_id = ? AND state = 'confirmed'",
                (owner_user_id, canonical_voice_id(voice_id)),
            )

    def reconcile(self, owner_user_id: str, provider_names: list[tuple[str, str | None]]) -> None:
        matches: dict[str, set[str]] = defaultdict(set)
        for voice_id, name in provider_names:
            if name and name.strip():
                matches[normalize_name_key(name)].add(canonical_voice_id(voice_id))
        with self.db.immediate_session() as conn:
            for key, ids in matches.items():
                if len(ids) == 1:
                    voice_id = next(iter(ids))
                    cached = conn.execute(
                        "SELECT display_name, availability FROM voice_registry "
                        "WHERE owner_user_id = ? AND voice_id = ?",
                        (owner_user_id, voice_id),
                    ).fetchone()
                    # A delayed provider snapshot cannot identify a different unknown creation.
                    if cached and (
                        cached["availability"] != "available"
                        or normalize_name_key(cached["display_name"] or "") != key
                    ):
                        continue
                    conn.execute(
                        "UPDATE voice_name_claims SET voice_id = ?, state = 'confirmed' "
                        "WHERE owner_user_id = ? AND name_key = ? AND state IN ('reserved', 'unknown')",
                        (voice_id, owner_user_id, key),
                    )
