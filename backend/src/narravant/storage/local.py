"""Local filesystem script storage client with atomic replace and path traversal guards."""

from __future__ import annotations

import json
import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from narravant.storage.protocol import ScriptStorageClient, StoredObjectMetadata

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


class LocalScriptStorageClient(ScriptStorageClient):
    """Local filesystem implementation of ScriptStorageClient."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _validate_document_id(self, document_id: str) -> None:
        if not document_id or not _SAFE_ID_PATTERN.match(document_id):
            raise ValueError(f"Invalid document_id (path traversal prevention): {document_id!r}")

    def _resolve_safe_path(self, relative_path: Path) -> Path:
        resolved = (self.base_dir / relative_path).resolve()
        try:
            resolved.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected: {relative_path}") from exc
        return resolved

    def _atomic_write_bytes(self, target_path: Path, data: bytes) -> str:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            dir=target_path.parent,
            prefix=f".tmp_{target_path.name}_",
            delete=False,
        )
        temp_path = Path(temp_file.name)
        try:
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_path, target_path)
        except Exception:
            temp_file.close()
            if temp_path.exists():
                temp_path.unlink()
            raise
        return sha256(data).hexdigest()

    def write_structured_script(
        self,
        document_id: str,
        version_id: int,
        data: dict[str, Any],
        if_generation_match: int = 0,
    ) -> StoredObjectMetadata:
        self._validate_document_id(document_id)
        target = self._resolve_safe_path(Path("scripts") / document_id / f"v{version_id}.json")
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        digest = self._atomic_write_bytes(target, encoded)
        return StoredObjectMetadata(
            uri=f"file://{target}",
            generation=version_id,
            sha256=digest,
        )

    def read_structured_script(
        self,
        document_id: str,
        version_id: int,
    ) -> tuple[dict[str, Any], StoredObjectMetadata]:
        self._validate_document_id(document_id)
        target = self._resolve_safe_path(Path("scripts") / document_id / f"v{version_id}.json")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Script file not found: {target}")
        content = target.read_bytes()
        digest = sha256(content).hexdigest()
        data = json.loads(content.decode("utf-8"))
        return data, StoredObjectMetadata(
            uri=f"file://{target}",
            generation=version_id,
            sha256=digest,
        )

    def write_search_text(
        self,
        document_id: str,
        text: str,
        if_generation_match: int = 0,
    ) -> StoredObjectMetadata:
        self._validate_document_id(document_id)
        target = self._resolve_safe_path(Path("search_texts") / f"{document_id}.txt")
        digest = self._atomic_write_bytes(target, text.encode("utf-8"))
        return StoredObjectMetadata(
            uri=f"file://{target}",
            generation=1,
            sha256=digest,
        )

    def read_search_text(self, document_id: str) -> str | None:
        self._validate_document_id(document_id)
        target = self._resolve_safe_path(Path("search_texts") / f"{document_id}.txt")
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def search_text_uri(self, document_id: str) -> str:
        self._validate_document_id(document_id)
        target = self._resolve_safe_path(Path("search_texts") / f"{document_id}.txt")
        return f"file://{target}"

    def _uri_to_path(self, uri: str) -> Path:
        raw_path = uri[len("file://") :] if uri.startswith("file://") else uri
        target = Path(raw_path).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected in URI: {uri}") from exc
        return target

    def stat_uri(self, uri: str) -> StoredObjectMetadata:
        target = self._uri_to_path(uri)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Stored file not found: {uri}")
        content = target.read_bytes()
        return StoredObjectMetadata(
            uri=uri,
            generation=1,
            sha256=sha256(content).hexdigest(),
        )

    def delete_uri(self, uri: str, if_generation_match: int = 0) -> None:
        target = self._uri_to_path(uri)
        if target.exists():
            target.unlink()
