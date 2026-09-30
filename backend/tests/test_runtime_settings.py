"""Runtime configuration and public error envelope contract tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from fastapi import Request
from fastapi.exceptions import RequestValidationError

from narravant.api.errors import ApiError, ApiErrorCode
from narravant.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENVIRONMENT = {
    "SQLITE_DB_PATH": "runtime/test.sqlite3",
    "GEMINI_API_KEY": "test-gemini-key",
    "LOG_LEVEL": "INFO",
}


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "missing_name",
    [
        "SQLITE_DB_PATH",
        "GEMINI_API_KEY",
        "LOG_LEVEL",
    ],
)
def test_settings_reject_required_environment_values(monkeypatch: pytest.MonkeyPatch, missing_name: str) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(SettingsError, match=missing_name):
        Settings.load(BACKEND_ROOT)


def test_settings_resolves_relative_runtime_paths_from_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from narravant.core.settings import Settings

    _set_required_environment(monkeypatch)

    settings = Settings.load(BACKEND_ROOT)

    repository_root = BACKEND_ROOT.parent
    assert settings.sqlite_db_path == repository_root / "runtime/test.sqlite3"
    assert settings.local_storage_path == repository_root / "runtime/storage"


def test_settings_loads_gemini_models_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from narravant.core.settings import Settings

    _set_required_environment(monkeypatch)
    settings = Settings.load(BACKEND_ROOT)
    assert settings.gemini_api_key == "test-gemini-key"
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.gemini_tts_model == "gemini-3.8-flash-tts"

    monkeypatch.setenv("GEMINI_MODEL", "custom-model")
    monkeypatch.setenv("GEMINI_TTS_MODEL", "custom-tts-model")
    custom_settings = Settings.load(BACKEND_ROOT)
    assert custom_settings.gemini_model == "custom-model"
    assert custom_settings.gemini_tts_model == "custom-tts-model"


def test_settings_loads_supported_gemini_generation_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini 3.8へ渡す有効な生成・timeout設定をconfig.yamlから読み込む。"""
    from narravant.core.settings import Settings

    _set_required_environment(monkeypatch)

    settings = Settings.load(BACKEND_ROOT)

    assert settings.gemini_connection_timeout_seconds == 300
    assert settings.gemini_chunk_timeout_seconds == 60
    assert settings.gemini_processing_timeout_seconds == 1800
    assert settings.gemini_max_output_tokens == 65536
    assert settings.gemini_max_main_characters == 6
    assert settings.gemini_generation_max_attempts == 6
    assert settings.gemini_voice_design_max_attempts == 3
    assert settings.gemini_retry_backoff_seconds == 3
    assert settings.playback_tts_max_attempts == 6
    assert settings.playback_scene_pause_duration_ms == 3000
    assert settings.emotion_arc_max_points == 36
    assert settings.gemini_thinking_level == "LOW"


def test_settings_rejects_missing_required_yaml_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config.pop("sse")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match="sse.heartbeat_interval_seconds"):
        Settings.load(tmp_path)


@pytest.mark.parametrize("key", ["tts_max_attempts", "scene_pause_duration_ms"])
def test_settings_requires_playback_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["playback"].pop(key)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match=f"playback.{key}"):
        Settings.load(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "analysis_max_attempts",
        "generation_max_attempts",
        "voice_design_max_attempts",
        "retry_backoff_seconds",
        "connection_timeout_seconds",
        "chunk_timeout_seconds",
        "processing_timeout_seconds",
        "max_output_tokens",
        "max_main_characters",
        "thinking_level",
    ],
)
def test_settings_rejects_missing_gemini_analysis_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"].pop(key)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match=f"gemini.{key}"):
        Settings.load(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "analysis_max_attempts",
        "generation_max_attempts",
        "voice_design_max_attempts",
        "retry_backoff_seconds",
        "connection_timeout_seconds",
        "chunk_timeout_seconds",
        "processing_timeout_seconds",
        "max_output_tokens",
        "max_main_characters",
    ],
)
@pytest.mark.parametrize("value", [0, "three"])
def test_settings_rejects_invalid_positive_gemini_integer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: int | str, key: str
) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"][key] = value
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match=f"gemini.{key}"):
        Settings.load(tmp_path)


def test_settings_rejects_invalid_gemini_thinking_level(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"]["thinking_level"] = "minimal"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match="gemini.thinking_level"):
        Settings.load(tmp_path)


@pytest.mark.parametrize("value", [0, "thirty-six"])
def test_settings_rejects_invalid_emotion_arc_max_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: int | str
) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["emotion_arc"]["max_points"] = value
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match="emotion_arc.max_points"):
        Settings.load(tmp_path)


def test_settings_rejects_missing_emotion_arc_max_points(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["emotion_arc"].pop("max_points")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match="emotion_arc.max_points"):
        Settings.load(tmp_path)


def test_settings_requires_valid_logging_level(monkeypatch: pytest.MonkeyPatch) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(SettingsError, match="LOG_LEVEL"):
        Settings.load(BACKEND_ROOT)


def test_runtime_logging_keeps_external_http_transport_logs_at_warning() -> None:
    from narravant.main import configure_runtime_logging

    logger_names = (
        "narravant",
        "urllib3",
        "httpcore",
        "httpx",
        "charset_normalizer",
        "asyncio",
        "uvicorn.access",
    )
    original_levels = {name: logging.getLogger(name).level for name in logger_names}
    try:
        configure_runtime_logging("DEBUG")

        assert logging.getLogger("narravant").level == logging.DEBUG
        assert all(logging.getLogger(name).level == logging.WARNING for name in logger_names[1:])
    finally:
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_settings_loads_valence_search_similarity_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from narravant.core.settings import Settings

    _set_required_environment(monkeypatch)
    settings = Settings.load(BACKEND_ROOT)
    assert settings.valence_similarity_threshold == 0.5
    assert settings.valence_max_arc_distance == 0.5


@pytest.mark.parametrize("invalid_threshold", [-0.1, 1.1, "not-a-number"])
def test_settings_rejects_invalid_valence_similarity_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_threshold: object
) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["valence_search"]["similarity_threshold"] = invalid_threshold
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match="valence_search.similarity_threshold"):
        Settings.load(tmp_path)


@pytest.mark.asyncio
async def test_request_validation_uses_common_error_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="narravant.api.errors")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/documents/import",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=diagnostic"),
                (b"content-length", b"123"),
            ],
        }
    )
    handler = app.exception_handlers[RequestValidationError]
    response = await handler(
        request,
        RequestValidationError([{"type": "greater_than", "loc": ("query", "limit")}]),
    )

    assert response.status_code == 422
    body = response.body.decode("utf-8")
    assert '"error"' in body
    assert '"VALIDATION_ERROR"' in body
    assert '"detail"' not in body
    assert "path=/api/v1/documents/import" in caplog.text
    assert "content_type=multipart/form-data; boundary=diagnostic" in caplog.text


@pytest.mark.asyncio
async def test_known_upload_validation_error_logs_request_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="narravant.api.errors")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/documents/import",
            "headers": [],
        }
    )
    handler = app.exception_handlers[ApiError]

    response = await handler(
        request,
        ApiError(422, ApiErrorCode.VALIDATION_ERROR, "対応していないファイル形式です。"),
    )

    assert response.status_code == 422
    assert "path=/api/v1/documents/import" in caplog.text
    assert "code=VALIDATION_ERROR" in caplog.text


def test_settings_loads_narrative_adaptation_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from narravant.core.settings import Settings

    _set_required_environment(monkeypatch)
    settings = Settings.load(BACKEND_ROOT)

    assert settings.gemini_text_reader_source_unit_max_characters == 24000
    assert settings.gemini_text_reader_source_unit_overlap_characters == 3000
    assert settings.gemini_pdf_classifier_page_window == 8
    assert settings.gemini_pdf_reader_page_window == 6
    assert settings.gemini_pdf_reader_page_overlap == 1
    assert settings.gemini_reader_refinement_max_attempts == 1
    assert settings.gemini_scene_regeneration_max_attempts == 1
    assert settings.gemini_classification_max_output_tokens == 2048
    assert settings.gemini_reader_max_output_tokens == 8192
    assert settings.gemini_planning_max_output_tokens == 16384
    assert settings.gemini_scene_writing_max_output_tokens == 16384
    assert settings.gemini_verification_max_output_tokens == 4096


@pytest.mark.parametrize(
    "key",
    [
        "text_reader_source_unit_max_characters",
        "text_reader_source_unit_overlap_characters",
        "pdf_classifier_page_window",
        "pdf_reader_page_window",
        "pdf_reader_page_overlap",
        "reader_refinement_max_attempts",
        "scene_regeneration_max_attempts",
        "classification_max_output_tokens",
        "reader_max_output_tokens",
        "planning_max_output_tokens",
        "scene_writing_max_output_tokens",
        "verification_max_output_tokens",
    ],
)
def test_settings_rejects_missing_narrative_adaptation_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)
    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"].pop(key)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SettingsError, match=f"gemini.{key}"):
        Settings.load(tmp_path)


def test_settings_rejects_invalid_overlap_relationships(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from narravant.core.settings import Settings, SettingsError

    _set_required_environment(monkeypatch)

    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"]["text_reader_source_unit_overlap_characters"] = 24000
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(
        SettingsError,
        match="gemini.text_reader_source_unit_overlap_characters must be less than",
    ):
        Settings.load(tmp_path)

    config = yaml.safe_load((BACKEND_ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["gemini"]["pdf_reader_page_overlap"] = 6
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(
        SettingsError,
        match="gemini.pdf_reader_page_overlap must be less than pdf_reader_page_window",
    ):
        Settings.load(tmp_path)


def test_get_narrative_adaptation_service_constructs_with_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from narravant.api.dependencies import get_narrative_adaptation_service
    from narravant.core.settings import Settings
    from narravant.services.narrative_adaptation import NarrativeAdaptationService
    from narravant.services.vertex_analysis import VertexDocumentAnalyzer

    _set_required_environment(monkeypatch)
    settings = Settings.load(BACKEND_ROOT)
    analyzer = MagicMock(spec=VertexDocumentAnalyzer)

    service = get_narrative_adaptation_service(settings, analyzer)
    assert isinstance(service, NarrativeAdaptationService)
    assert (
        service.config.text_reader_source_unit_max_characters == settings.gemini_text_reader_source_unit_max_characters
    )
    assert (
        service.config.text_reader_source_unit_overlap_characters
        == settings.gemini_text_reader_source_unit_overlap_characters
    )
    assert service.config.pdf_classifier_page_window == settings.gemini_pdf_classifier_page_window
    assert service.config.pdf_reader_page_window == settings.gemini_pdf_reader_page_window
    assert service.config.pdf_reader_page_overlap == settings.gemini_pdf_reader_page_overlap
    assert service.config.reader_refinement_max_attempts == settings.gemini_reader_refinement_max_attempts
    assert service.config.scene_regeneration_max_attempts == settings.gemini_scene_regeneration_max_attempts
    assert service.config.classification_max_output_tokens == settings.gemini_classification_max_output_tokens
    assert service.config.reader_max_output_tokens == settings.gemini_reader_max_output_tokens
    assert service.config.planning_max_output_tokens == settings.gemini_planning_max_output_tokens
    assert service.config.scene_writing_max_output_tokens == settings.gemini_scene_writing_max_output_tokens
    assert service.config.verification_max_output_tokens == settings.gemini_verification_max_output_tokens
