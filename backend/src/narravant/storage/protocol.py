"""Protocol defining script and metadata storage operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredObjectMetadata:
    uri: str
    generation: int
    sha256: str


class ScriptStorageClient(Protocol):
    """Protocol defining script and metadata storage operations."""

    def write_structured_script(
        self,
        document_id: str,
        version_id: int,
        data: dict[str, Any],
        if_generation_match: int = 0,
    ) -> StoredObjectMetadata:
        """Write structured v1.json document."""
        ...

    def read_structured_script(
        self,
        document_id: str,
        version_id: int,
    ) -> tuple[dict[str, Any], StoredObjectMetadata]:
        """Read structured v1.json document and return (data, metadata)."""
        ...

    def write_search_text(
        self,
        document_id: str,
        text: str,
        if_generation_match: int = 0,
    ) -> StoredObjectMetadata:
        """Write plain text representation."""
        ...

    def read_search_text(self, document_id: str) -> str | None:
        """Read plain text representation."""
        ...

    def search_text_uri(self, document_id: str) -> str:
        """Return the backend-specific URI of the document's search text object."""
        ...

    def stat_uri(self, uri: str) -> StoredObjectMetadata:
        """Get metadata for a stored URI."""
        ...

    def delete_uri(self, uri: str, if_generation_match: int = 0) -> None:
        """Delete stored object at URI."""
        ...
