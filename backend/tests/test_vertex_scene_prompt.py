"""小説の場面生成は出来事を保ち、本文の再現を要求しない。"""

from time import monotonic
from types import SimpleNamespace

import pytest

from narravant.core.content_language import source_language_instruction
from narravant.services.narrative_adaptation import PlannedScene
from narravant.services.vertex_analysis import CollectedStream, VertexDocumentAnalyzer


@pytest.mark.parametrize("language", ["en", "ja", "und"])
def test_scene_writer_requests_adaptation_in_fresh_wording(language):
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.scene_writing_max_output_tokens = 1000
    requests = []

    def collect(**kwargs):
        requests.append(kwargs)
        return CollectedStream("INT. ROOM - DAY #1#\n\n@MAYA\nWe can leave now.", 50, ("STOP",))

    analyzer._collect_stream_text = collect
    plan = PlannedScene(
        scene_number=1,
        heading="INT. ROOM - DAY #1#",
        source_unit_ids=["unit1"],
        retained_event_ids=["event1"],
        omitted_event_ids=[],
        purpose="Maya decides to leave.",
        characters=["MAYA"],
        causal_notes="The room is no longer safe.",
        dependency_scene_numbers=[],
    )
    result = analyzer.write_scene(plan, [], [], deadline=123.0, source_language=language)

    prompt = requests[0]["contents"]
    assert "fresh wording" in prompt
    assert "paraphrase narration and dialogue" in prompt
    assert "spoken content" not in prompt
    assert "original" not in prompt.lower()
    assert "元の" not in prompt
    assert source_language_instruction(language) in prompt
    assert "Never append dialogue, action, or a description to a cue line" in prompt
    assert requests[0]["processing_deadline"] == 123.0
    assert result.startswith("INT.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "finish_reason", "corrected"),
    [
        ("write_scene", "RECITATION", True),
        ("normalize_screenplay_text", "RECITATION", False),
        ("write_scene", "SAFETY", False),
    ],
)
async def test_scene_retry_keeps_failure_and_attempt_limit(operation, finish_reason, corrected):
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.generation_max_attempts = 3
    analyzer.retry_backoff_seconds = 0
    analyzer.model = "fake"
    analyzer.thinking_level = "LOW"
    analyzer.connection_timeout_seconds = 60
    analyzer.chunk_timeout_seconds = 60
    analyzer.processing_timeout_seconds = 60
    analyzer._request_config = lambda *args, **kwargs: {"max_output_tokens": 1000}
    analyzer._finish_reasons = lambda _: (finish_reason,)
    requests = []

    async def generate(**kwargs):
        requests.append(kwargs["contents"])

        async def chunks():
            yield SimpleNamespace(text="Blocked partial scene.")

        return chunks()

    async def close():
        pass

    analyzer._create_client = lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate), aclose=close)
    )
    progress = []
    with pytest.raises(ValueError, match=f"finish_reason={finish_reason}"):
        await analyzer._collect_stream_text_async(
            contents="Synthetic scene plan",
            on_progress=progress.append,
            config=None,
            operation=operation,
            attempt=1,
            processing_deadline=monotonic() + 60,
            allow_non_stop=False,
        )

    assert len(requests) == 3
    assert requests[0] == "Synthetic scene plan"
    assert ("ANTI-RECITATION:" in requests[1]) is corrected
    assert requests[2] == requests[1]
    assert all("Blocked partial scene." not in request for request in requests)
    assert progress == sorted(progress)
