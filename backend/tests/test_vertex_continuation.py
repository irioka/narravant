"""Continuation decisions are contract tests around the bounded state machine."""

from __future__ import annotations

import pytest

from narravant.services.vertex_analysis import CollectedStream, VertexDocumentAnalyzer


def _analyzer(results: list[CollectedStream]) -> VertexDocumentAnalyzer:
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.import_continuation_max_attempts = 2
    analyzer.import_max_fountain_characters = 100_000
    analyzer.processing_timeout_seconds = 60
    analyzer._collect_stream_text = lambda **_: results.pop(0)  # type: ignore[method-assign]
    return analyzer


def test_continuation_accepts_only_one_final_marker_after_max_tokens() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "# TYPE: SCREENPLAY\nTitle: Story\n\nINT. ROOM - DAY #1#\n\nOne.",
                50,
                ("MAX_TOKENS",),
            ),
            CollectedStream(
                "\n\nEXT. STREET - NIGHT #2#\n\nTwo.\n=== NARRAVANT FOUNTAIN COMPLETE ===",
                60,
                ("STOP",),
            ),
        ]
    )
    anchors: list[str] = []

    result = analyzer._convert_with_continuation(
        initial_contents="initial",
        continuation_contents=lambda anchor: anchors.append(anchor) or "next",
        on_progress=lambda _: None,
        operation="test",
        processing_deadline=None,
    )

    assert "# TYPE:" not in result
    assert "#1#" in result and "#2#" in result
    assert anchors and "#1#" in anchors[0]


def test_one_shot_conversion_canonically_renumbers_source_revision_gaps() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "# TYPE: SCREENPLAY\nTitle: Story\n\nINT. ROOM - DAY #7#\n\nOne.\n\n"
                "EXT. STREET - NIGHT #12#\n\nTwo.\n=== NARRAVANT FOUNTAIN COMPLETE ===",
                100,
                ("STOP",),
            )
        ]
    )

    result = analyzer._convert_with_continuation(
        initial_contents="initial",
        continuation_contents=lambda _: "next",
        on_progress=lambda _: None,
        operation="test",
        processing_deadline=None,
    )

    assert "#1#" in result and "#2#" in result
    assert "#7#" not in result and "#12#" not in result


def test_continuation_rejects_a_repeated_scene_number_across_the_merge_boundary() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "# TYPE: SCREENPLAY\nTitle: Story\n\nINT. ROOM - DAY #1#\n\nOne.",
                50,
                ("MAX_TOKENS",),
            ),
            CollectedStream(
                "\n\nEXT. STREET - NIGHT #1#\n\nTwo.\n=== NARRAVANT FOUNTAIN COMPLETE ===",
                60,
                ("STOP",),
            ),
        ]
    )

    with pytest.raises(ValueError, match="duplicate or non-contiguous"):
        analyzer._convert_with_continuation(
            initial_contents="initial",
            continuation_contents=lambda _: "next",
            on_progress=lambda _: None,
            operation="test",
            processing_deadline=None,
        )


@pytest.mark.parametrize(
    "stream",
    [
        CollectedStream(
            "# TYPE: SCREENPLAY\nTitle: Story\n\nINT. ROOM - DAY #1#\n\nOne.",
            50,
            ("SAFETY",),
        ),
        CollectedStream(
            "# TYPE: SCREENPLAY\nTitle: Story\n\nINT. ROOM - DAY #1#\n\nOne.\n=== NARRAVANT FOUNTAIN COMPLETE ===",
            50,
            ("MAX_TOKENS",),
        ),
    ],
)
def test_continuation_fails_closed_for_terminal_or_invalid_streams(
    stream: CollectedStream,
) -> None:
    analyzer = _analyzer([stream])
    with pytest.raises(ValueError):
        analyzer._convert_with_continuation(
            initial_contents="initial",
            continuation_contents=lambda _: "next",
            on_progress=lambda _: None,
            operation="test",
            processing_deadline=None,
        )


def test_continuation_without_type_header_succeeds_when_not_required() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "Title: Story\n\nINT. ROOM - DAY #1#\n\nOne.\n=== NARRAVANT FOUNTAIN COMPLETE ===",
                50,
                ("STOP",),
            )
        ]
    )
    result = analyzer._convert_with_continuation(
        initial_contents="initial",
        continuation_contents=lambda _: "next",
        on_progress=lambda _: None,
        operation="test",
        processing_deadline=None,
        require_type_header=False,
    )
    assert "#1#" in result
    assert "# TYPE:" not in result


def test_continuation_accepts_marker_wrapped_in_markdown_code_fence() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "```fountain\nTitle: Story\n\nINT. ROOM - DAY #1#\n\nOne.\n=== NARRAVANT FOUNTAIN COMPLETE ===\n```",
                60,
                ("STOP",),
            )
        ]
    )
    result = analyzer._convert_with_continuation(
        initial_contents="initial",
        continuation_contents=lambda _: "next",
        on_progress=lambda _: None,
        operation="test",
        processing_deadline=None,
        require_type_header=False,
    )
    assert "#1#" in result
    assert "```" not in result
    assert "=== NARRAVANT FOUNTAIN COMPLETE ===" not in result


def test_continuation_accepts_marker_followed_by_trailing_comments() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "Title: Story\n\nINT. ROOM - DAY #1#\n\nOne.\n=== NARRAVANT FOUNTAIN COMPLETE ===\n以上で完了です。",
                60,
                ("STOP",),
            )
        ]
    )
    result = analyzer._convert_with_continuation(
        initial_contents="initial",
        continuation_contents=lambda _: "next",
        on_progress=lambda _: None,
        operation="test",
        processing_deadline=None,
        require_type_header=False,
    )
    assert "#1#" in result
    assert "以上で完了です" not in result


def test_continuation_rejects_marker_when_scenes_follow_it() -> None:
    analyzer = _analyzer(
        [
            CollectedStream(
                "Title: Story\n\nINT. ROOM - DAY #1#\n\nOne.\n"
                "=== NARRAVANT FOUNTAIN COMPLETE ===\n\n"
                "EXT. PARK - NIGHT #2#\nTwo.",
                80,
                ("STOP",),
            )
        ]
    )
    with pytest.raises(ValueError, match="must be the final isolated line"):
        analyzer._convert_with_continuation(
            initial_contents="initial",
            continuation_contents=lambda _: "next",
            on_progress=lambda _: None,
            operation="test",
            processing_deadline=None,
            require_type_header=False,
        )


def test_is_transient_vertex_error_classification() -> None:
    from google.genai.errors import APIError

    from narravant.services.vertex_analysis import (
        VertexStreamTimeoutError,
        is_transient_vertex_error,
    )

    assert is_transient_vertex_error(VertexStreamTimeoutError("chunk", 60.0)) is True
    assert is_transient_vertex_error(APIError(429, {})) is True
    assert is_transient_vertex_error(APIError(408, {})) is True
    assert is_transient_vertex_error(APIError(503, {})) is True
    assert is_transient_vertex_error(APIError(500, {})) is True
    assert is_transient_vertex_error(APIError(400, {})) is False
    assert is_transient_vertex_error(APIError(404, {})) is False
    assert is_transient_vertex_error(ConnectionResetError("reset")) is True
    assert is_transient_vertex_error(BrokenPipeError("pipe")) is True
    assert is_transient_vertex_error(ValueError("invalid")) is False


@pytest.mark.asyncio
async def test_collect_stream_text_retries_transient_with_proportional_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from google.genai.errors import APIError

    from narravant.services.vertex_analysis import VertexDocumentAnalyzer

    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.generation_max_attempts = 6
    analyzer.retry_backoff_seconds = 3
    analyzer.model = "gemini-3.8-flash"
    analyzer.thinking_level = "LOW"
    analyzer.connection_timeout_seconds = 300
    analyzer.chunk_timeout_seconds = 60
    analyzer._request_config = lambda config, **_: {"max_output_tokens": 1000}  # type: ignore[assignment]
    analyzer._safe_response_summary = lambda _: "summary"  # type: ignore[assignment]

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    attempts_count = 0

    class DummyChunk:
        text = "Hello world"

    class FailingClient:
        class aio:
            @staticmethod
            async def aclose():
                pass

            class models:
                @staticmethod
                async def generate_content_stream(*args, **kwargs):
                    nonlocal attempts_count
                    attempts_count += 1
                    if attempts_count < 3:
                        raise APIError(429, {})

                    async def gen():
                        yield DummyChunk()

                    return gen()

    analyzer._create_client = lambda: FailingClient()  # type: ignore[assignment]
    analyzer._finish_reasons = lambda _: ("STOP",)  # type: ignore[assignment]
    analyzer._token_counts = lambda _: (10, 5, 0, 15)  # type: ignore[assignment]

    result = await analyzer._collect_stream_text_async(
        contents="test",
        on_progress=lambda _: None,
        config=None,
        operation="test_op",
        attempt=1,
        processing_deadline=1000000000.0,
        allow_non_stop=False,
    )

    assert result.text == "Hello world"
    assert attempts_count == 3  # 1 initial + 2 retries
    # generation_attempt 1: 1 * 3 = 3.0, generation_attempt 2: 2 * 3 = 6.0
    assert sleep_calls == [3.0, 6.0]
