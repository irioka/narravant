"""Tests for LocalScriptStorageClient."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from narravant.storage.local import LocalScriptStorageClient


@pytest.fixture
def storage(tmp_path: Path) -> LocalScriptStorageClient:
    return LocalScriptStorageClient(base_dir=tmp_path / "storage")


def test_write_and_read_structured_script_matches(storage: LocalScriptStorageClient) -> None:
    data = {"schema_version": 1, "title": "Test Screenplay", "scenes": []}
    metadata = storage.write_structured_script("doc-1", 1, data, if_generation_match=0)

    assert metadata.uri.startswith("file://")
    assert len(metadata.sha256) == 64
    assert metadata.generation == 1

    loaded_data, loaded_metadata = storage.read_structured_script("doc-1", 1)
    assert loaded_data == data
    assert loaded_metadata.sha256 == metadata.sha256
    assert loaded_metadata.uri == metadata.uri


def test_versions_are_immutable_separate_files(storage: LocalScriptStorageClient) -> None:
    v1_data = {"schema_version": 1, "title": "V1"}
    v2_data = {"schema_version": 1, "title": "V2"}

    storage.write_structured_script("doc-1", 1, v1_data, if_generation_match=0)
    storage.write_structured_script("doc-1", 2, v2_data, if_generation_match=0)

    loaded_v1, _ = storage.read_structured_script("doc-1", 1)
    loaded_v2, _ = storage.read_structured_script("doc-1", 2)
    assert loaded_v1["title"] == "V1"
    assert loaded_v2["title"] == "V2"


def test_path_traversal_is_rejected(storage: LocalScriptStorageClient) -> None:
    data = {"schema_version": 1}
    for bad_id in ("../../etc", "../outside", "foo/../../bar", ".."):
        with pytest.raises(ValueError, match="[Pp]ath traversal|Invalid document"):
            storage.write_structured_script(bad_id, 1, data, if_generation_match=0)

        with pytest.raises(ValueError, match="[Pp]ath traversal|Invalid document"):
            storage.read_structured_script(bad_id, 1)


def test_missing_script_raises_file_not_found(storage: LocalScriptStorageClient) -> None:
    with pytest.raises(FileNotFoundError):
        storage.read_structured_script("nonexistent", 1)


def test_search_text_write_and_read(storage: LocalScriptStorageClient) -> None:
    meta = storage.write_search_text("doc-1", "Plain search text", if_generation_match=0)
    assert meta.uri.startswith("file://")
    assert len(meta.sha256) == 64

    content = storage.read_search_text("doc-1")
    assert content == "Plain search text"

    missing = storage.read_search_text("missing-doc")
    assert missing is None


def test_stat_and_delete_uri(storage: LocalScriptStorageClient) -> None:
    meta = storage.write_structured_script("doc-1", 1, {"test": True}, if_generation_match=0)
    stat_meta = storage.stat_uri(meta.uri)
    assert stat_meta.sha256 == meta.sha256

    storage.delete_uri(meta.uri, if_generation_match=0)
    with pytest.raises(FileNotFoundError):
        storage.stat_uri(meta.uri)


def test_failed_write_cleans_up_temporary_file(
    storage: LocalScriptStorageClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_replace(src, dst):
        raise OSError("Disk failure simulation")

    monkeypatch.setattr(os, "replace", fake_replace)

    target_dir = storage.base_dir / "scripts" / "doc-err"
    with pytest.raises(OSError, match="Disk failure simulation"):
        storage.write_structured_script("doc-err", 1, {"title": "Fail"}, if_generation_match=0)

    # Verify no temp files and no target file remained
    if target_dir.exists():
        files = list(target_dir.iterdir())
        assert files == []


def test_corrupted_file_raises_and_is_not_empty_overwritten(
    storage: LocalScriptStorageClient,
) -> None:
    # Write valid v1
    storage.write_structured_script("doc-1", 1, {"title": "Valid"}, if_generation_match=0)

    # Corrupt v1 on disk
    target = storage.base_dir / "scripts" / "doc-1" / "v1.json"
    target.write_bytes(b"{invalid json content")

    # Reading corrupt file should raise json.JSONDecodeError (not return empty or overwrite)
    with pytest.raises(json.JSONDecodeError):
        storage.read_structured_script("doc-1", 1)
