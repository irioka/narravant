"""Validated runtime settings for the FastAPI application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from narravant.core.valence_pattern import (
    ValencePattern,
    ValencePatternConfigurationError,
    load_valence_patterns,
)
from narravant.core.valence_vector import (
    ValenceVectorizationError,
    ValenceVectorizationProfile,
    load_valence_vectorization_profile,
)


class SettingsError(RuntimeError):
    """Raised when a required startup setting is missing or invalid."""


LOG_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise SettingsError(f"Required environment variable {name} is not configured")
    return value.strip()


def _required_yaml_value(data: dict[str, Any], *path: str) -> Any:
    value: Any = data
    for part in path:
        if not isinstance(value, dict) or part not in value:
            dotted_path = ".".join(path)
            raise SettingsError(f"Required config.yaml key {dotted_path} is not configured")
        value = value[part]
    if value is None:
        dotted_path = ".".join(path)
        raise SettingsError(f"Required config.yaml key {dotted_path} is not configured")
    return value


@dataclass(frozen=True)
class Settings:
    """All values needed to construct the production runtime dependencies."""

    log_level: str
    sqlite_db_path: Path
    local_storage_path: Path
    gemini_api_key: str
    gemini_model: str
    gemini_tts_model: str
    gemini_analysis_max_attempts: int
    gemini_generation_max_attempts: int
    gemini_voice_design_max_attempts: int
    gemini_retry_backoff_seconds: int
    gemini_connection_timeout_seconds: int
    gemini_chunk_timeout_seconds: int
    gemini_processing_timeout_seconds: int
    gemini_max_output_tokens: int
    gemini_max_main_characters: int
    gemini_thinking_level: str
    gemini_import_continuation_max_attempts: int
    gemini_import_max_fountain_characters: int
    gemini_text_reader_source_unit_max_characters: int
    gemini_text_reader_source_unit_overlap_characters: int
    gemini_pdf_classifier_page_window: int
    gemini_pdf_reader_page_window: int
    gemini_pdf_reader_page_overlap: int
    gemini_reader_refinement_max_attempts: int
    gemini_scene_regeneration_max_attempts: int
    gemini_classification_max_output_tokens: int
    gemini_reader_max_output_tokens: int
    gemini_planning_max_output_tokens: int
    gemini_scene_writing_max_output_tokens: int
    gemini_verification_max_output_tokens: int
    playback_tts_max_attempts: int
    playback_scene_pause_duration_ms: int
    emotion_arc_max_points: int
    sse_heartbeat_interval_seconds: int
    sse_poll_interval_milliseconds: int
    file_import_max_size_mb: int
    file_import_pdf_max_pages: int
    file_import_fdx_max_depth: int
    file_import_txt_minimum_confidence: float
    file_import_draft_ttl_seconds: int
    valence_vectorization: ValenceVectorizationProfile
    valence_patterns: tuple[ValencePattern, ...]
    valence_similarity_threshold: float

    @property
    def valence_max_arc_distance(self) -> float:
        return round(1.0 - self.valence_similarity_threshold, 6)

    @property
    def valence_vector_profile_fingerprint(self) -> str:
        return self.valence_vectorization.fingerprint()

    @classmethod
    def load(cls, backend_root: Path) -> Settings:
        """Load required environment and YAML values without applying defaults.

        ``config.yaml`` lives under ``backend/``, while the project ``.env``
        lives at the repository root.  Relative local-runtime paths therefore
        resolve from the repository root as users naturally expect (for
        example ``./runtime/storage`` is ``<repo>/runtime/storage``), not from
        the backend package directory.
        """
        config_path = backend_root / "config.yaml"
        try:
            raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SettingsError(f"Unable to read required config file {config_path}") from exc
        except yaml.YAMLError as exc:
            raise SettingsError(f"Invalid YAML in {config_path}") from exc

        if not isinstance(raw_config, dict):
            raise SettingsError(f"Required config file {config_path} must contain a mapping")

        raw_db_path = _required_environment("SQLITE_DB_PATH")
        db_path = Path(raw_db_path)
        if not db_path.is_absolute():
            db_path = backend_root.parent / db_path

        raw_storage_path = (os.getenv("LOCAL_STORAGE_PATH") or "").strip() or "runtime/storage"
        storage_path = Path(raw_storage_path)
        if not storage_path.is_absolute():
            storage_path = backend_root.parent / storage_path

        gemini_api_key = _required_environment("GEMINI_API_KEY")
        gemini_model = (os.getenv("GEMINI_MODEL") or "").strip() or "gemini-3.8-flash"
        gemini_tts_model = (os.getenv("GEMINI_TTS_MODEL") or "").strip() or "gemini-3.8-flash-tts"

        try:
            valence_vectorization = load_valence_vectorization_profile(
                _required_yaml_value(raw_config, "valence_vectorization")
            )
        except ValenceVectorizationError as exc:
            raise SettingsError(f"Invalid valence_vectorization: {exc}") from exc

        try:
            valence_patterns = load_valence_patterns(
                _required_yaml_value(raw_config, "valence_patterns"),
                value_min=valence_vectorization.value_min,
                value_max=valence_vectorization.value_max,
            )
        except ValencePatternConfigurationError as exc:
            raise SettingsError(f"Invalid valence_patterns: {exc}") from exc

        raw_threshold = _required_yaml_value(raw_config, "valence_search", "similarity_threshold")
        try:
            valence_similarity_threshold = float(raw_threshold)
        except (TypeError, ValueError) as exc:
            raise SettingsError("valence_search.similarity_threshold must be a numeric value") from exc
        if not 0.0 <= valence_similarity_threshold <= 1.0:
            raise SettingsError("valence_search.similarity_threshold must be between 0.0 and 1.0")

        log_level = _required_environment("LOG_LEVEL").upper()
        if log_level not in LOG_LEVEL_NAMES:
            raise SettingsError("LOG_LEVEL must be a standard Python log level")
        positive_gemini_integers: dict[str, int] = {}
        for key in (
            "analysis_max_attempts",
            "generation_max_attempts",
            "voice_design_max_attempts",
            "retry_backoff_seconds",
            "connection_timeout_seconds",
            "chunk_timeout_seconds",
            "processing_timeout_seconds",
            "max_output_tokens",
            "max_main_characters",
            "import_continuation_max_attempts",
            "import_max_fountain_characters",
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
        ):
            try:
                positive_gemini_integers[key] = int(_required_yaml_value(raw_config, "gemini", key))
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"gemini.{key} must be an integer") from exc
            if positive_gemini_integers[key] < 1:
                raise SettingsError(f"gemini.{key} must be at least 1")
        playback_tts_max_attempts = cls._required_positive_integer(raw_config, "playback", "tts_max_attempts")
        playback_scene_pause_duration_ms = cls._required_nonnegative_integer(
            raw_config, "playback", "scene_pause_duration_ms"
        )
        try:
            emotion_arc_max_points = int(_required_yaml_value(raw_config, "emotion_arc", "max_points"))
        except (TypeError, ValueError) as exc:
            raise SettingsError("emotion_arc.max_points must be an integer") from exc
        if emotion_arc_max_points < 1:
            raise SettingsError("emotion_arc.max_points must be at least 1")
        if (
            positive_gemini_integers["text_reader_source_unit_overlap_characters"]
            >= positive_gemini_integers["text_reader_source_unit_max_characters"]
        ):
            raise SettingsError(
                "gemini.text_reader_source_unit_overlap_characters "
                "must be less than text_reader_source_unit_max_characters"
            )
        if positive_gemini_integers["pdf_reader_page_overlap"] >= positive_gemini_integers["pdf_reader_page_window"]:
            raise SettingsError("gemini.pdf_reader_page_overlap must be less than pdf_reader_page_window")
        thinking_level = str(_required_yaml_value(raw_config, "gemini", "thinking_level")).upper()
        if thinking_level not in {"LOW", "MEDIUM", "HIGH"}:
            raise SettingsError("gemini.thinking_level must be LOW, MEDIUM, or HIGH")

        return cls(
            log_level=log_level,
            sqlite_db_path=db_path,
            local_storage_path=storage_path,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_tts_model=gemini_tts_model,
            gemini_analysis_max_attempts=positive_gemini_integers["analysis_max_attempts"],
            gemini_generation_max_attempts=positive_gemini_integers["generation_max_attempts"],
            gemini_voice_design_max_attempts=positive_gemini_integers["voice_design_max_attempts"],
            gemini_retry_backoff_seconds=positive_gemini_integers["retry_backoff_seconds"],
            gemini_connection_timeout_seconds=positive_gemini_integers["connection_timeout_seconds"],
            gemini_chunk_timeout_seconds=positive_gemini_integers["chunk_timeout_seconds"],
            gemini_processing_timeout_seconds=positive_gemini_integers["processing_timeout_seconds"],
            gemini_max_output_tokens=positive_gemini_integers["max_output_tokens"],
            gemini_max_main_characters=positive_gemini_integers["max_main_characters"],
            gemini_thinking_level=thinking_level,
            gemini_import_continuation_max_attempts=positive_gemini_integers["import_continuation_max_attempts"],
            gemini_import_max_fountain_characters=positive_gemini_integers["import_max_fountain_characters"],
            gemini_text_reader_source_unit_max_characters=positive_gemini_integers[
                "text_reader_source_unit_max_characters"
            ],
            gemini_text_reader_source_unit_overlap_characters=positive_gemini_integers[
                "text_reader_source_unit_overlap_characters"
            ],
            gemini_pdf_classifier_page_window=positive_gemini_integers["pdf_classifier_page_window"],
            gemini_pdf_reader_page_window=positive_gemini_integers["pdf_reader_page_window"],
            gemini_pdf_reader_page_overlap=positive_gemini_integers["pdf_reader_page_overlap"],
            gemini_reader_refinement_max_attempts=positive_gemini_integers["reader_refinement_max_attempts"],
            gemini_scene_regeneration_max_attempts=positive_gemini_integers["scene_regeneration_max_attempts"],
            gemini_classification_max_output_tokens=positive_gemini_integers["classification_max_output_tokens"],
            gemini_reader_max_output_tokens=positive_gemini_integers["reader_max_output_tokens"],
            gemini_planning_max_output_tokens=positive_gemini_integers["planning_max_output_tokens"],
            gemini_scene_writing_max_output_tokens=positive_gemini_integers["scene_writing_max_output_tokens"],
            gemini_verification_max_output_tokens=positive_gemini_integers["verification_max_output_tokens"],
            playback_tts_max_attempts=playback_tts_max_attempts,
            playback_scene_pause_duration_ms=playback_scene_pause_duration_ms,
            emotion_arc_max_points=emotion_arc_max_points,
            sse_heartbeat_interval_seconds=int(_required_yaml_value(raw_config, "sse", "heartbeat_interval_seconds")),
            sse_poll_interval_milliseconds=int(_required_yaml_value(raw_config, "sse", "poll_interval_milliseconds")),
            file_import_max_size_mb=int(_required_yaml_value(raw_config, "file_import", "max_size_mb")),
            file_import_pdf_max_pages=int(_required_yaml_value(raw_config, "file_import", "pdf_max_pages")),
            file_import_fdx_max_depth=int(_required_yaml_value(raw_config, "file_import", "fdx_max_depth")),
            file_import_txt_minimum_confidence=float(
                _required_yaml_value(raw_config, "file_import", "txt_minimum_confidence")
            ),
            file_import_draft_ttl_seconds=int(_required_yaml_value(raw_config, "file_import", "draft_ttl_seconds")),
            valence_vectorization=valence_vectorization,
            valence_patterns=valence_patterns,
            valence_similarity_threshold=valence_similarity_threshold,
        )

    @staticmethod
    def _required_positive_integer(data: dict[str, Any], *path: str) -> int:
        try:
            value = int(_required_yaml_value(data, *path))
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{'.'.join(path)} must be an integer") from exc
        if value < 1:
            raise SettingsError(f"{'.'.join(path)} must be at least 1")
        return value

    @staticmethod
    def _required_nonnegative_integer(data: dict[str, Any], *path: str) -> int:
        try:
            value = int(_required_yaml_value(data, *path))
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{'.'.join(path)} must be an integer") from exc
        if value < 0:
            raise SettingsError(f"{'.'.join(path)} must be at least 0")
        return value
