"""Gemini Developer API のクライアント切替と安全なエラー処理の検証。"""

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
        gemini_source_voice_traits_max_attempts=2,
        gemini_source_voice_traits_max_output_tokens=4096,
        gemini_analysis_max_attempts=3,
        gemini_generation_max_attempts=3,
        gemini_retry_backoff_seconds=1,
        gemini_connection_timeout_seconds=30,
        gemini_chunk_timeout_seconds=10,
        gemini_processing_timeout_seconds=60,
        gemini_http_timeout_milliseconds=1800000,
        gemini_minimum_output_tokens=1,
        gemini_max_output_tokens=8192,
        gemini_max_main_characters=6,
        gemini_thinking_level="LOW",
        gemini_safety_threshold="BLOCK_NONE",
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
        gemini_verification_max_attempts=3,
        emotion_arc_max_points=36,
    )


def test_analyzer_uses_configured_model_and_api_key(dummy_settings) -> None:
    analyzer = VertexDocumentAnalyzer(dummy_settings)
    assert analyzer.model == "gemini-3.8-flash"
    assert analyzer.api_key == "synthetic-test-api-key-12345"


def test_analyzer_uses_resolved_timeouts_without_replacing_processing_deadline(dummy_settings) -> None:
    dummy_settings.gemini_model = "gemini-4-argon"
    dummy_settings.gemini_connection_timeout_seconds = 1301
    dummy_settings.gemini_chunk_timeout_seconds = 1302
    analyzer = VertexDocumentAnalyzer(dummy_settings)
    assert analyzer.connection_timeout_seconds == 1301
    assert analyzer.chunk_timeout_seconds == 1302
    assert analyzer.processing_timeout_seconds == 60


@pytest.mark.parametrize("threshold", ["BLOCK_NONE", "BLOCK_ONLY_HIGH"])
def test_analyzer_passes_model_to_client_stream(dummy_settings, threshold) -> None:
    dummy_settings.gemini_safety_threshold = threshold
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
    from narravant.services.narrative_adaptation import PlannedScene, SceneContext

    plan = PlannedScene(
        scene_number=1, heading="INT. ROOM - DAY #1#", source_unit_ids=["su_test"],
        retained_event_ids=["ev_test"], omitted_event_ids=[], purpose="Synthetic scene",
        characters=["MAYA"], causal_notes="Synthetic cause",
    )
    context = SceneContext(scene_number=1, source_unit_ids=("su_test",), retained_events=(), source_material=())
    assert "INT. ROOM - DAY #1#" in analyzer.write_scene(plan, context)
    assert dummy_client.last_kwargs["model"] == "gemini-3.8-flash"
    safety = dummy_client.last_kwargs["config"]["safety_settings"]
    assert {item["category"]: item["threshold"] for item in safety} == {
        "HARM_CATEGORY_HARASSMENT": threshold,
        "HARM_CATEGORY_HATE_SPEECH": threshold,
        "HARM_CATEGORY_SEXUALLY_EXPLICIT": threshold,
        "HARM_CATEGORY_DANGEROUS_CONTENT": threshold,
    }
    from google.genai import types

    parsed = types.GenerateContentConfig(**dummy_client.last_kwargs["config"])
    assert all(item.threshold == types.HarmBlockThreshold(threshold) for item in parsed.safety_settings)


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
