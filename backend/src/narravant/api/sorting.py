"""Validation for the document-list SQL sort query contract."""

from __future__ import annotations

from collections.abc import Sequence

from narravant.db.database import DocumentSort

ALLOWED_DOCUMENT_SORT_FIELDS = frozenset({"title", "owner_email", "shared_count", "updated_at", "version_id"})
ALLOWED_SORT_DIRECTIONS = frozenset({"asc", "desc"})
MAX_DOCUMENT_SORT_TERMS = 5


def parse_document_sorts(raw_sorts: Sequence[str]) -> list[DocumentSort] | None:
    """Parse repeated ``sort=field:direction`` values into allow-listed repository values."""
    if not raw_sorts:
        return None
    terms = [term for raw_sort in raw_sorts for term in raw_sort.split(",")]
    if len(terms) > MAX_DOCUMENT_SORT_TERMS:
        raise ValueError(f"sort accepts at most {MAX_DOCUMENT_SORT_TERMS} terms")

    parsed: list[DocumentSort] = []
    seen_fields: set[str] = set()
    for raw_sort in terms:
        field, separator, direction = raw_sort.partition(":")
        normalized_field = field.strip()
        normalized_direction = direction.strip().lower()
        if not separator or not normalized_field or not normalized_direction:
            raise ValueError("sort must use field:asc or field:desc")
        if normalized_field not in ALLOWED_DOCUMENT_SORT_FIELDS:
            raise ValueError(f"unsupported sort field: {normalized_field}")
        if normalized_direction not in ALLOWED_SORT_DIRECTIONS:
            raise ValueError(f"unsupported sort direction: {normalized_direction}")
        if normalized_field in seen_fields:
            raise ValueError(f"duplicate sort field: {normalized_field}")
        seen_fields.add(normalized_field)
        parsed.append(DocumentSort(normalized_field, normalized_direction))  # type: ignore[arg-type]
    return parsed
