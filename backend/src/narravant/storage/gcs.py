"""GCS storage client with Compare-And-Swap (CAS) precondition support.

Implements generation precondition checks to prevent lost updates on GCS objects.
Translates PreconditionFailed errors into GcsConflictError (mapped to HTTP 409).
Provides both real Google Cloud Storage operations and an in-memory test implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol


class GcsConflictError(Exception):
    """Raised when a GCS generation precondition fails (CAS conflict / 409)."""

    def __init__(
        self,
        message: str,
        uri: str,
        expected_generation: int | None,
        actual_generation: int | None = None,
    ) -> None:
        super().__init__(message)
        self.uri = uri
        self.expected_generation = expected_generation
        self.actual_generation = actual_generation


@dataclass
class StoredObject:
    """Represents a stored GCS object with its content and generation."""

    data: bytes
    generation: int
    content_type: str = "application/json"


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
        if_generation_match: int,
    ) -> StoredObjectMetadata:
        """Write structured v1.json document with generation check."""
        ...

    def read_structured_script(
        self,
        document_id: str,
        version_id: int,
    ) -> tuple[dict[str, Any], StoredObjectMetadata]:
        """Read structured v1.json document and return (data, generation)."""
        ...

    def write_search_text(self, document_id: str, text: str, if_generation_match: int) -> StoredObjectMetadata:
        """Write plain text representation for Vertex AI Search."""
        ...

    def read_search_text(self, document_id: str) -> str | None:
        """Read plain text representation for Vertex AI Search."""
        ...

    def stat_uri(self, uri: str) -> StoredObjectMetadata: ...

    def delete_uri(self, uri: str, if_generation_match: int) -> None: ...


class InMemoryScriptStorageClient:
    """In-memory mock storage client for local testing with CAS simulation."""

    def __init__(self, bucket_name: str = "example-narravant-bucket") -> None:
        self.bucket_name = bucket_name
        self._objects: dict[str, StoredObject] = {}
        self._next_generation = 1

    def _get_key(self, path: str) -> str:
        return f"gs://{self.bucket_name}/{path}"

    def stat_uri(self, uri: str) -> StoredObjectMetadata:
        obj = self._objects.get(uri)
        if obj is None:
            raise FileNotFoundError(f"GCS object not found: {uri}")
        return StoredObjectMetadata(uri, obj.generation, sha256(obj.data).hexdigest())

    def delete_uri(self, uri: str, if_generation_match: int) -> None:
        metadata = self.stat_uri(uri)
        if metadata.generation != if_generation_match:
            raise GcsConflictError("CAS conflict on delete", uri, if_generation_match, metadata.generation)
        del self._objects[uri]

    def write_structured_script(
        self,
        document_id: str,
        version_id: int,
        data: dict[str, Any],
        if_generation_match: int = 0,
    ) -> StoredObjectMetadata:
        path = f"scripts/{document_id}/v{version_id}.json"
        uri = self._get_key(path)

        existing = self._objects.get(uri)
        current_gen = existing.generation if existing else 0

        if if_generation_match != current_gen:
            raise GcsConflictError(
                f"CAS conflict on {uri}: expected gen {if_generation_match}, but current is {current_gen}",
                uri=uri,
                expected_generation=if_generation_match,
                actual_generation=current_gen,
            )

        new_gen = self._next_generation
        self._next_generation += 1

        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self._objects[uri] = StoredObject(data=payload, generation=new_gen, content_type="application/json")
        return StoredObjectMetadata(uri=uri, generation=new_gen, sha256=sha256(payload).hexdigest())

    def read_structured_script(
        self,
        document_id: str,
        version_id: int,
    ) -> tuple[dict[str, Any], StoredObjectMetadata]:
        path = f"scripts/{document_id}/v{version_id}.json"
        uri = self._get_key(path)
        obj = self._objects.get(uri)
        if obj is None:
            raise FileNotFoundError(f"GCS object not found: {uri}")
        data = json.loads(obj.data.decode("utf-8"))
        return data, StoredObjectMetadata(uri=uri, generation=obj.generation, sha256=sha256(obj.data).hexdigest())

    def write_search_text(self, document_id: str, text: str, if_generation_match: int) -> StoredObjectMetadata:
        path = f"search_texts/{document_id}.txt"
        uri = self._get_key(path)
        existing = self._objects.get(uri)
        current_gen = existing.generation if existing else 0
        if if_generation_match != current_gen:
            raise GcsConflictError("CAS conflict on search text", uri, if_generation_match, current_gen)
        new_gen = self._next_generation
        self._next_generation += 1
        payload = text.encode("utf-8")
        self._objects[uri] = StoredObject(data=payload, generation=new_gen, content_type="text/plain")
        return StoredObjectMetadata(uri=uri, generation=new_gen, sha256=sha256(payload).hexdigest())

    def search_text_uri(self, document_id: str) -> str:
        return self._get_key(f"search_texts/{document_id}.txt")

    def read_search_text(self, document_id: str) -> str | None:
        path = f"search_texts/{document_id}.txt"
        uri = self._get_key(path)
        obj = self._objects.get(uri)
        if obj is None:
            return None
        return obj.data.decode("utf-8")
