"""Tests for Gemini Developer API client switching and safe error handling (Task 5 / A5)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narravant.services.vertex_analysis import (
    VertexDocumentAnalyzer,
    is_transient_vertex_error,
)


@pytest.fixture
def dummy_settings():
    return SimpleNamespace(
        gemini_api_key="synthetic-test-api-key-12345",
        gemini_model="gemini-3.8-flash",
        gemini_analysis_max_attempts=3,
        gemini_generation_max_attempts=3,
        gemini_retry_backoff_seconds=1,
        gemini_connection_timeout_seconds=30,
        gemini_chunk_timeout_seconds=10,
        gemini_processing_timeout_seconds=60,
        gemini_max_output_tokens=8192,
        gemini_max_main_characters=6,
        gemini_thinking_level="LOW",
        gemini_import_continuation_max_attempts=3,
        gemini_import_max_fountain_characters=50000,
        gemini_text_reader_source_unit_max_characters=24000,
        gemini_text_reader_source_unit_overlap_characters=3000,
        gemini_pdf_classifier_page_window=8,
        gemini_pdf_reader_page_window=6,
        gemini_pdf_reader_page_overlap=1,
        gemini_reader_refinement_max_attempts=1,
        gemini_scene_regeneration_max_attempts=1,
        gemini_classification_max_output_tokens=2048,
        gemini_reader_max_output_tokens=8192,
        gemini_planning_max_output_tokens=8192,
        gemini_scene_writing_max_output_tokens=8192,
        gemini_verification_max_output_tokens=4096,
        emotion_arc_max_points=36,
    )


def test_analyzer_uses_configured_model_and_api_key(dummy_settings) -> None:
    analyzer = VertexDocumentAnalyzer(dummy_settings)
    assert analyzer.model == "gemini-3.8-flash"
    assert analyzer.api_key == "synthetic-test-api-key-12345"


def test_analyzer_passes_model_to_client_stream(dummy_settings) -> None:
    analyzer = VertexDocumentAnalyzer(dummy_settings)

    class DummyChunk:
        text = "# TYPE: SCREENPLAY\nINT. ROOM - DAY #1#\n\n=== NARRAVANT FOUNTAIN COMPLETE ==="

    class DummyClient:
        def __init__(self):
            self.last_kwargs = {}
            self.aio = self

            class Models:
                def __init__(self, outer):
                    self.outer = outer

                async def generate_content_stream(self, *args, **kwargs):
                    self.outer.last_kwargs = kwargs

                    async def gen():
                        yield DummyChunk()

                    return gen()

            self.models = Models(self)
            self.aclose = AsyncMock()

    dummy_client = DummyClient()
    analyzer._create_client = lambda: dummy_client
    analyzer._finish_reasons = lambda _: ("STOP",)
    analyzer._token_counts = lambda _: (10, 5, 0, 15)

    result = analyzer.convert_text_to_fountain("INT. ROOM - DAY", lambda x: None)
    assert "INT. ROOM - DAY #1#" in result
    assert dummy_client.last_kwargs["model"] == "gemini-3.8-flash"


def test_auth_rejection_fails_immediately_without_leaking_key(dummy_settings, caplog: pytest.LogCaptureFixture) -> None:
    from google.genai.errors import APIError

    analyzer = VertexDocumentAnalyzer(dummy_settings)

    auth_error = APIError(403, {"error": {"message": "API_KEY_INVALID: The provided API key is invalid."}})
    assert not is_transient_vertex_error(auth_error)

    class FailingClient:
        def __init__(self):
            self.aio = self

            class Models:
                async def generate_content_stream(self, *args, **kwargs):
                    raise auth_error

            self.models = Models()
            self.aclose = AsyncMock()

    analyzer._create_client = lambda: FailingClient()

    caplog.set_level(logging.DEBUG)
    with pytest.raises(APIError) as caught:
        analyzer.convert_text_to_fountain("INT. SECRET MANUSCRIPT - DAY", lambda x: None)

    assert caught.value.code == 403
    # Check that the secret api key and manuscript content are not logged by our code
    assert dummy_settings.gemini_api_key not in caplog.text
    assert "SECRET MANUSCRIPT" not in caplog.text
