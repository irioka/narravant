"""Coordinate provider voice state, local registry, document usage, and playback checks."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from narravant.api.voice_schemas import (
    VoiceListResponse,
    VoiceSummary,
    VoiceUsage,
    VoiceUsageResponse,
)
from narravant.db.database import DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.services.voice_design import VoiceDesignClient, VoiceDesignError
from narravant.services.voice_models import (
    ProviderVoice,
    StoredVoiceRecord,
    VoiceAvailability,
    VoiceExpiryState,
    VoicePage,
    canonical_voice_id,
    utc_iso,
)
from narravant.storage.gcs import ScriptStorageClient

logger = logging.getLogger(__name__)

_PROVIDER_TIMEOUT_SECONDS = 30.0
_MAX_CONCURRENT_GETS = 4
_MAX_PROVIDER_PAGES = 100
_DOCUMENT_PAGE_SIZE = 50
_VOICE_CACHE_TTL = timedelta(minutes=5)
_EXPIRING_SOON = timedelta(days=30)


class VoiceManagementError(Exception):
    """Safe, typed error that the voice API can translate to an HTTP response."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


class VoiceManagementService:
    """Reconcile a single local user's Gemini voices with saved document assignments."""

    def __init__(
        self,
        provider: VoiceDesignClient,
        registry: VoiceRegistryRepository,
        documents: DocumentRepository,
        storage: ScriptStorageClient,
        *,
        local_owner_id: str,
        owner_locks: dict[str, asyncio.Lock] | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.documents = documents
        self.storage = storage
        self.local_owner_id = local_owner_id
        self.owner_locks = owner_locks if owner_locks is not None else {}

    def _lock(self, owner_user_id: str) -> asyncio.Lock:
        return self.owner_locks.setdefault(owner_user_id, asyncio.Lock())

    def lock_for_owner(self, owner_user_id: str) -> asyncio.Lock:
        """Return the shared owner lock for mutation paths outside this service."""
        self._require_local_owner(owner_user_id)
        return self._lock(owner_user_id)

    async def list_voices(self, owner_user_id: str) -> VoiceListResponse:
        self._require_local_owner(owner_user_id)
        async with self._lock(owner_user_id):
            provider_voices = await self._list_all_provider_voices()
            usages, usages_complete = self._scan_usages(owner_user_id)
            existing = {record.voice_id: record for record in self.registry.list(owner_user_id)}
            usages_by_voice = self._group_usages(usages)
            provider_ids = set(provider_voices)
            known_ids = provider_ids | set(existing) | set(usages_by_voice)
            foreign_ids = self.registry.ids_registered_for_other_owners(owner_user_id, known_ids)
            provider_voices = {
                voice_id: voice for voice_id, voice in provider_voices.items() if voice_id not in foreign_ids
            }
            known_ids -= foreign_ids
            provider_ids -= foreign_ids

            # A complete page traversal is required before applying any sync
            # result. Known list entries can now safely refresh local metadata.
            checked_at = utc_iso()
            refreshed: dict[str, StoredVoiceRecord] = {}
            display_names = {voice_id: voice.display_name for voice_id, voice in provider_voices.items()}
            for voice_id, provider_voice in provider_voices.items():
                record = self._record_from_provider(
                    owner_user_id,
                    provider_voice,
                    checked_at,
                    existing.get(voice_id),
                )
                self.registry.upsert(record)
                refreshed[voice_id] = record

            missing_ids = sorted(known_ids - provider_ids)
            outcomes = await self._get_many(missing_ids)
            complete = usages_complete
            summaries: list[VoiceSummary] = []
            for voice_id in sorted(known_ids):
                record = refreshed.get(voice_id) or existing.get(voice_id)
                outcome = outcomes.get(voice_id)
                if voice_id not in provider_ids:
                    if isinstance(outcome, ProviderVoice):
                        display_names[voice_id] = outcome.display_name
                    record = self._apply_get_outcome(
                        owner_user_id,
                        voice_id,
                        outcome,
                        record,
                        usages_by_voice.get(voice_id, []),
                        checked_at,
                    )
                    if outcome is None or isinstance(outcome, Exception) and not self._is_not_found(outcome):
                        complete = False
                if record is None:
                    record = self._unknown_record(owner_user_id, voice_id, checked_at)
                summaries.append(
                    self._summary(
                        record,
                        len(usages_by_voice.get(voice_id, [])) if usages_complete else None,
                        display_name=display_names.get(voice_id),
                    )
                )
            return VoiceListResponse(voices=summaries, checked_at=checked_at, complete=complete)

    async def get_voice(self, owner_user_id: str, voice_id: str) -> VoiceSummary:
        self._require_local_owner(owner_user_id)
        canonical_id = self._canonical_id(voice_id)
        self._reject_foreign_voice_id(owner_user_id, canonical_id)
        async with self._lock(owner_user_id):
            record = self.registry.get(owner_user_id, canonical_id)
            usages: list[VoiceUsage] = []
            if record is None:
                all_usages, usages_complete = self._scan_usages(owner_user_id)
                usages = [usage for item_id, usage in all_usages if item_id == canonical_id]
                if not usages_complete or not usages:
                    raise self._not_found()
            try:
                provider_voice = await asyncio.wait_for(
                    self.provider.get_voice(canonical_id), timeout=_PROVIDER_TIMEOUT_SECONDS
                )
            except VoiceDesignError as exc:
                if self._is_not_found(exc):
                    checked_at = utc_iso()
                    if record is None:
                        record = self._unknown_record(owner_user_id, canonical_id, checked_at)
                    record = self._with_availability(record, "missing", checked_at)
                    self.registry.upsert(record)
                    return self._summary(record, None)
                raise self._provider_error(exc) from exc
            except TimeoutError as exc:
                raise self._unavailable() from exc

            if self._canonical_id(provider_voice.voice_id) != canonical_id:
                raise self._unavailable()
            checked_at = utc_iso()
            record = self._record_from_provider(
                owner_user_id,
                provider_voice,
                checked_at,
                record,
                speaker_label=usages[0].speaker if usages else None,
            )
            self.registry.upsert(record)
            usage_count, complete = self._usage_count(owner_user_id, canonical_id)
            return self._summary(record, usage_count if complete else None, display_name=provider_voice.display_name)

    async def get_usages(self, owner_user_id: str, voice_id: str) -> VoiceUsageResponse:
        self._require_local_owner(owner_user_id)
        canonical_id = self._canonical_id(voice_id)
        self._reject_foreign_voice_id(owner_user_id, canonical_id)
        record = self.registry.get(owner_user_id, canonical_id)
        all_usages, complete = self._scan_usages(owner_user_id)
        matching = [usage for item_id, usage in all_usages if item_id == canonical_id]
        if record is None and not matching:
            raise self._not_found()
        return VoiceUsageResponse(usages=matching, complete=complete)

    async def delete_voice(self, owner_user_id: str, voice_id: str) -> None:
        self._require_local_owner(owner_user_id)
        canonical_id = self._canonical_id(voice_id)
        async with self._lock(owner_user_id):
            self._reject_foreign_voice_id(owner_user_id, canonical_id)
            record = self.registry.get(owner_user_id, canonical_id)
            usages, complete = self._scan_usages(owner_user_id)
            if record is None and not any(item_id == canonical_id for item_id, _ in usages):
                raise self._not_found()
            if not complete:
                raise VoiceManagementError(
                    "VOICE_USAGE_UNAVAILABLE",
                    503,
                    "利用先を完全に確認できないため、声を削除できません。",
                )
            try:
                await asyncio.wait_for(self.provider.delete_voice(canonical_id), timeout=_PROVIDER_TIMEOUT_SECONDS)
            except VoiceDesignError as exc:
                if not self._is_not_found(exc):
                    raise self._provider_error(exc, deleting=True) from exc
                # A provider 404 means the requested remote deletion is already complete.
            except TimeoutError as exc:
                # DELETE is never retried: its remote result may be unknown.
                raise self._unavailable() from exc
            try:
                self.registry.delete(owner_user_id, canonical_id)
            except Exception as exc:
                logger.warning("Voice registry delete sync failed exception_type=%s", type(exc).__name__)
                raise VoiceManagementError(
                    "VOICE_REGISTRY_SYNC_FAILED",
                    503,
                    "Gemini 上の声は削除されましたが、ローカル一覧の同期に失敗しました。",
                ) from exc

    async def validate_voice_ids(
        self,
        owner_user_id: str,
        voice_ids: list[str],
        *,
        allow_current_assignment: bool = True,
    ) -> None:
        """Verify custom IDs before starting TTS; prebuilt catalog names remain compatible."""
        self._require_local_owner(owner_user_id)
        canonical_ids: list[str] = []
        for voice_id in voice_ids:
            if voice_id.startswith("voices/") and not voice_id.startswith("voices/voice_"):
                self._canonical_id(voice_id)
            if not voice_id.startswith(("voice_", "voices/voice_")):
                continue
            canonical_ids.append(self._canonical_id(voice_id))
        canonical_ids = list(dict.fromkeys(canonical_ids))
        if not canonical_ids:
            return

        async with self._lock(owner_user_id):
            foreign_ids = self.registry.ids_registered_for_other_owners(owner_user_id, canonical_ids)
            if foreign_ids:
                raise self._not_found()
            records = {record.voice_id: record for record in self.registry.list(owner_user_id)}
            if allow_current_assignment:
                usages, usages_complete = self._scan_usages(owner_user_id)
            else:
                usages, usages_complete = [], True
            usages_by_voice = self._group_usages(usages)
            unowned = set(canonical_ids) - set(records) - set(usages_by_voice)
            if unowned or (not usages_complete and set(canonical_ids) - set(records)):
                raise self._not_found()

            outcomes = await self._get_many(canonical_ids)
            for voice_id in canonical_ids:
                record = records.get(voice_id)
                outcome = outcomes.get(voice_id)
                if isinstance(outcome, ProviderVoice):
                    if self._canonical_id(outcome.voice_id) != voice_id:
                        raise self._unavailable()
                    checked_at = utc_iso()
                    voice_usages = usages_by_voice.get(voice_id, [])
                    updated = self._record_from_provider(
                        owner_user_id,
                        outcome,
                        checked_at,
                        record,
                        speaker_label=voice_usages[0].speaker if voice_usages else None,
                    )
                    self.registry.upsert(updated)
                    if self._expiry_state(updated) == "expired":
                        raise VoiceManagementError(
                            "VOICE_EXPIRED",
                            409,
                            "声の有効期限が切れています。別の声を割り当ててください。",
                        )
                elif isinstance(outcome, Exception) and self._is_not_found(outcome):
                    checked_at = utc_iso()
                    updated = self._with_availability(
                        record or self._unknown_record(owner_user_id, voice_id, checked_at),
                        "missing",
                        checked_at,
                    )
                    self.registry.upsert(updated)
                    raise VoiceManagementError(
                        "VOICE_NOT_FOUND",
                        409,
                        "声が Gemini 上に存在しません。別の声を割り当ててください。",
                    )
                elif isinstance(outcome, VoiceDesignError):
                    raise self._provider_error(outcome) from outcome
                else:
                    raise self._unavailable()

    async def _list_all_provider_voices(self) -> dict[str, ProviderVoice]:
        try:
            return await asyncio.wait_for(self._collect_provider_voices(), timeout=_PROVIDER_TIMEOUT_SECONDS)
        except VoiceDesignError as exc:
            raise self._provider_error(exc) from exc
        except TimeoutError as exc:
            raise self._unavailable() from exc

    async def _collect_provider_voices(self) -> dict[str, ProviderVoice]:
        voices: dict[str, ProviderVoice] = {}
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(_MAX_PROVIDER_PAGES):
            page: VoicePage = await self.provider.list_voices(page_token)
            for voice in page.voices:
                canonical_id = self._canonical_id(voice.voice_id)
                if voice.voice_type in {"prompted", "replicated"}:
                    voices[canonical_id] = voice
            next_token = page.next_page_token
            if not next_token:
                return voices
            if next_token in seen_tokens or next_token == page_token:
                raise VoiceDesignError(
                    "Gemini Voices API returned a repeated page token",
                    provider_code="INVALID_RESPONSE",
                )
            seen_tokens.add(next_token)
            page_token = next_token
        raise VoiceDesignError(
            "Gemini Voices API exceeded the page limit",
            provider_code="INVALID_RESPONSE",
        )

    async def _get_many(self, voice_ids: list[str]) -> dict[str, ProviderVoice | Exception | None]:
        if not voice_ids:
            return {}
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_GETS)

        async def fetch(voice_id: str) -> ProviderVoice | Exception:
            async with semaphore:
                try:
                    voice = await self.provider.get_voice(voice_id)
                    if self._canonical_id(voice.voice_id) != voice_id:
                        raise VoiceDesignError(
                            "Gemini Voices API returned a different voice ID",
                            provider_code="INVALID_RESPONSE",
                        )
                    return voice
                except Exception as exc:
                    return exc

        tasks = [fetch(voice_id) for voice_id in voice_ids]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=_PROVIDER_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return dict.fromkeys(voice_ids, None)
        return dict(zip(voice_ids, results, strict=True))

    def _apply_get_outcome(
        self,
        owner_user_id: str,
        voice_id: str,
        outcome: ProviderVoice | Exception | None,
        record: StoredVoiceRecord | None,
        usages: list[VoiceUsage],
        checked_at: str,
    ) -> StoredVoiceRecord | None:
        if isinstance(outcome, ProviderVoice):
            if self._canonical_id(outcome.voice_id) != voice_id:
                return record
            updated = self._record_from_provider(
                owner_user_id,
                outcome,
                checked_at,
                record,
                speaker_label=usages[0].speaker if usages else None,
            )
            self.registry.upsert(updated)
            return updated
        if isinstance(outcome, Exception) and self._is_not_found(outcome):
            updated = self._with_availability(
                record or self._unknown_record(owner_user_id, voice_id, checked_at),
                "missing",
                checked_at,
            )
            self.registry.upsert(updated)
            return updated
        if record is not None:
            # Preserve the confirmed cache values in storage. The response is
            # stale because this Get failed, even when the previous check was
            # recent enough to otherwise appear current.
            return StoredVoiceRecord(**{**record.__dict__, "availability": "unknown"})
        if record is None:
            return self._unknown_record(owner_user_id, voice_id, checked_at)
        return record

    def _scan_usages(self, owner_user_id: str) -> tuple[list[tuple[str, VoiceUsage]], bool]:
        usages: list[tuple[str, VoiceUsage]] = []
        seen: set[tuple[str, str, str]] = set()
        complete = True
        offset = 0
        total: int | None = None
        while total is None or offset < total:
            try:
                documents, total_count = self.documents.list_documents(
                    limit=_DOCUMENT_PAGE_SIZE,
                    offset=offset,
                    viewer_user_id=owner_user_id,
                )
            except Exception:
                return usages, False
            total = total_count
            if not documents:
                if offset < total:
                    complete = False
                break
            for document in documents:
                document_owner_id = document.get("owner_user_id")
                if document_owner_id is None:
                    complete = False
                    continue
                if str(document_owner_id) != owner_user_id:
                    # A document visible through sharing is not owned by this
                    # voice library and must not appear in its usage results.
                    continue
                try:
                    data, _ = self.storage.read_structured_script(
                        str(document["document_id"]), int(document["current_version_id"])
                    )
                except Exception:
                    complete = False
                    continue
                assignments = data.get("voice_assignments", [])
                if not isinstance(assignments, list):
                    complete = False
                    continue
                for assignment in assignments:
                    if not isinstance(assignment, dict):
                        complete = False
                        continue
                    raw_id = assignment.get("voice_id")
                    speaker = assignment.get("speaker")
                    if not isinstance(raw_id, str) or not isinstance(speaker, str):
                        complete = False
                        continue
                    if not raw_id.startswith(("voice_", "voices/")):
                        # Existing prebuilt catalog assignments are outside the
                        # custom voice library and cannot make its scan incomplete.
                        continue
                    try:
                        voice_id = self._canonical_id(raw_id)
                    except VoiceManagementError:
                        complete = False
                        continue
                    key = (str(document["document_id"]), speaker, voice_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    usages.append(
                        (
                            voice_id,
                            VoiceUsage(
                                document_id=key[0],
                                title=str(document.get("title") or ""),
                                speaker=speaker,
                            ),
                        )
                    )
            offset += len(documents)
            if offset < total and len(documents) < _DOCUMENT_PAGE_SIZE:
                complete = False
                break
        usages.sort(key=lambda item: (item[1].title.casefold(), item[1].document_id, item[1].speaker))
        return usages, complete

    def _usage_count(self, owner_user_id: str, voice_id: str) -> tuple[int, bool]:
        usages, complete = self._scan_usages(owner_user_id)
        grouped = self._group_usages(usages)
        return len(grouped.get(voice_id, [])), complete

    @staticmethod
    def _group_usages(usages: list[tuple[str, VoiceUsage]]) -> dict[str, list[VoiceUsage]]:
        grouped: dict[str, list[VoiceUsage]] = {}
        for voice_id, usage in usages:
            grouped.setdefault(voice_id, []).append(usage)
        return grouped

    def _record_from_provider(
        self,
        owner_user_id: str,
        provider_voice: ProviderVoice,
        checked_at: str,
        existing: StoredVoiceRecord | None,
        *,
        speaker_label: str | None = None,
    ) -> StoredVoiceRecord:
        canonical_id = self._canonical_id(provider_voice.voice_id)
        return StoredVoiceRecord(
            owner_user_id=owner_user_id,
            voice_id=canonical_id,
            speaker_label=(existing.speaker_label if existing else None) or speaker_label,
            voice_traits=existing.voice_traits if existing else None,
            language_code=provider_voice.language_code or (existing.language_code if existing else None),
            created_at=existing.created_at if existing else None,
            created_at_source=existing.created_at_source if existing else "unknown",
            registered_at=existing.registered_at if existing else checked_at,
            expire_time=utc_iso(provider_voice.expire_time) if provider_voice.expire_time else None,
            last_checked_at=checked_at,
            availability="available",
        )

    def _unknown_record(self, owner_user_id: str, voice_id: str, registered_at: str) -> StoredVoiceRecord:
        return StoredVoiceRecord(
            owner_user_id=owner_user_id,
            voice_id=self._canonical_id(voice_id),
            speaker_label=None,
            voice_traits=None,
            language_code=None,
            created_at=None,
            created_at_source="unknown",
            registered_at=registered_at,
            expire_time=None,
            last_checked_at=None,
            availability="unknown",
        )

    @staticmethod
    def _with_availability(
        record: StoredVoiceRecord,
        availability: VoiceAvailability,
        checked_at: str,
    ) -> StoredVoiceRecord:
        return StoredVoiceRecord(
            **{
                **record.__dict__,
                "availability": availability,
                "last_checked_at": checked_at,
            }
        )

    def _summary(
        self,
        record: StoredVoiceRecord,
        usage_count: int | None,
        *,
        display_name: str | None = None,
    ) -> VoiceSummary:
        return VoiceSummary(
            voice_id=record.voice_id,
            display_name=display_name,
            speaker_label=record.speaker_label,
            voice_traits=record.voice_traits,
            language_code=record.language_code,
            created_at=record.created_at,
            created_at_source=record.created_at_source,
            expire_time=record.expire_time,
            availability=record.availability,
            expiry_state=self._expiry_state(record),
            last_checked_at=record.last_checked_at,
            usage_count=usage_count,
        )

    @staticmethod
    def _expiry_state(record: StoredVoiceRecord) -> VoiceExpiryState:
        if record.availability == "missing":
            return "unknown"
        if record.availability == "unknown":
            return "stale"
        if record.last_checked_at:
            try:
                checked_at = datetime.fromisoformat(record.last_checked_at.replace("Z", "+00:00"))
                if checked_at.tzinfo is None:
                    checked_at = checked_at.replace(tzinfo=UTC)
                if datetime.now(UTC) - checked_at.astimezone(UTC) > _VOICE_CACHE_TTL:
                    return "stale"
            except ValueError:
                return "stale"
        if not record.expire_time:
            return "unknown"
        try:
            expiry = datetime.fromisoformat(record.expire_time.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
        except ValueError:
            return "unknown"
        remaining = expiry.astimezone(UTC) - datetime.now(UTC)
        if remaining <= timedelta(0):
            return "expired"
        if remaining <= _EXPIRING_SOON:
            return "expiring"
        return "valid"

    def _canonical_id(self, voice_id: str) -> str:
        try:
            return canonical_voice_id(voice_id)
        except ValueError as exc:
            raise VoiceManagementError(
                "VOICE_UNAVAILABLE",
                422,
                "無効な作成済みの声 ID です。",
            ) from exc

    def _reject_foreign_voice_id(self, owner_user_id: str, voice_id: str) -> None:
        if self.registry.ids_registered_for_other_owners(owner_user_id, [voice_id]):
            raise self._not_found()

    def _provider_error(self, error: VoiceDesignError, *, deleting: bool = False) -> VoiceManagementError:
        if self._is_not_found(error):
            return VoiceManagementError("VOICE_NOT_FOUND", 404, "作成済みの声が見つかりません。")
        if error.provider_status == 403 or error.provider_code in {"PERMISSION_DENIED", "FORBIDDEN"}:
            return VoiceManagementError(
                "VOICE_PROVIDER_PERMISSION_DENIED",
                502,
                "Gemini Voices API の操作が許可されませんでした。",
            )
        if error.provider_status in {502, 503, 504} or error.provider_code in {
            "NETWORK_ERROR",
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
        }:
            return self._unavailable()
        message = "Gemini 上の声の削除に失敗しました。" if deleting else "Gemini の声を確認できませんでした。"
        return VoiceManagementError("VOICE_PROVIDER_UNAVAILABLE", 503, message)

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        return isinstance(error, VoiceDesignError) and (
            error.provider_status == 404 or error.provider_code in {"NOT_FOUND", "VOICE_NOT_FOUND"}
        )

    def _require_local_owner(self, owner_user_id: str) -> None:
        if owner_user_id != self.local_owner_id:
            raise self._unavailable()

    @staticmethod
    def _not_found() -> VoiceManagementError:
        return VoiceManagementError("VOICE_NOT_FOUND", 404, "作成済みの声が見つかりません。")

    @staticmethod
    def _unavailable() -> VoiceManagementError:
        return VoiceManagementError(
            "VOICE_PROVIDER_UNAVAILABLE",
            503,
            "Gemini の声を確認できません。時間をおいて再試行してください。",
        )
