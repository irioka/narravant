from time import monotonic
from types import SimpleNamespace

import pytest

from narravant.api.playback import PlaybackStartRequest
from narravant.core.fountain import FountainParser
from narravant.services.narrative_adaptation import NarrativeAdaptationError
from narravant.services.performance_review import (
    InlineCue,
    PerformanceReviewConfig,
    PerformanceReviewResult,
    PerformanceReviewService,
    TurnPerformance,
    apply_performance_review,
)
from narravant.services.playback import build_playback_plan


@pytest.mark.parametrize(
    "source",
    [
        "INT. ROOM - DAY #1#\n\n@MINA\nHello ice cream.\n",
        "\r\nINT. ROOM - DAY #1#\r\n\r\n@ミナ\r\n待って😀。来るのね。\r\n",
        "INT. ROOM - DAY #1#\n\n@Narrator\nHello.\nWorld.\n",
    ],
)
def test_renderer_preserves_words_tags_and_untouched_material(source):
    text = FountainParser.parse(source).all_utterances()[0].text
    edit = TurnPerformance(
        scene_number=1,
        utterance_index=0,
        split_offsets=[],
        styles=["calm"],
        insertions=[InlineCue(offset=0, tag="breath")],
    )
    rendered = apply_performance_review(source, [edit])
    turns = FountainParser.parse(rendered).all_utterances()
    assert turns[0].text == "<breath>" + text
    assert turns[0].performance_direction == "calm"


@pytest.mark.parametrize("style", ["x" * 121, "calm\n@EVIL", "（別話者）"])
def test_renderer_rejects_control_line_injection(style):
    edit = TurnPerformance(scene_number=1, utterance_index=0, split_offsets=[], styles=[style], insertions=[])
    with pytest.raises(ValueError):
        apply_performance_review("INT. ROOM - DAY #1#\n\n@MINA\nHello.\n", [edit])


def test_existing_direction_and_pause_are_preserved():
    source = "INT. ROOM - DAY #1#\n\n@MINA\n(urgent)\nHello.<short pause> Wait.\n"
    edit = TurnPerformance(scene_number=1, utterance_index=0, split_offsets=[], styles=["calm"], insertions=[])
    with pytest.raises(ValueError, match="existing_direction"):
        apply_performance_review(source, [edit], preserve_existing_directions=True)
    edit.styles = ["urgent"]
    edit.insertions = [InlineCue(offset=6, tag="long pause")]
    with pytest.raises(ValueError):
        apply_performance_review(source, [edit])


class FakePerformanceClient:
    def __init__(self):
        self.windows = []
        self.scenes = []

    def summarize_performance_window(self, text, numbers, analysis, *, deadline, ensure_not_cancelled=None):
        self.windows.append((text, numbers))
        return {number: "静かな確認" for number in numbers}

    def review_scene_performance(self, number, utterances, context, **kwargs):
        self.scenes.append((number, context, kwargs))
        return PerformanceReviewResult(edits=[])


def test_context_covers_all_scenes_without_persistence_or_rewrite():
    client = FakePerformanceClient()
    service = PerformanceReviewService(client, PerformanceReviewConfig(window_max_characters=70))
    source = "INT. ROOM - DAY #1#\n\n@ミナ\n" + "待つの？" * 30 + "\n\nINT. ROAD - DAY #2#\n\n@ミナ\n行くのね。\n"
    result = service.review_performance(
        source,
        SimpleNamespace(metadata={"synopsis": "あらすじ"}),
        source_cards=(),
        deadline=monotonic() + 10,
        ensure_not_cancelled=lambda: None,
        on_progress=lambda _: None,
        preserve_existing_directions=True,
    )
    assert result == source
    assert [v[0] for v in client.scenes] == [1, 2]
    assert all(set(v[1].scene_intents) == {1, 2} for v in client.scenes)
    assert all(len(text) <= 70 for text, _ in client.windows)


def test_invalid_context_is_retried_with_fixed_reason_only(caplog):
    client = FakePerformanceClient()

    def invalid(*args, **kwargs):
        client.windows.append(None)
        return {999: "PRIVATE_SENTINEL"}

    client.summarize_performance_window = invalid
    service = PerformanceReviewService(client, PerformanceReviewConfig())
    with pytest.raises(NarrativeAdaptationError):
        service.review_performance(
            "INT. ROOM - DAY #1#\n\n@MINA\nHello.\n",
            SimpleNamespace(metadata={}),
            source_cards=(),
            deadline=monotonic() + 10,
            ensure_not_cancelled=lambda: None,
            on_progress=lambda _: None,
            preserve_existing_directions=False,
        )
    assert len(client.windows) == 2
    assert "PRIVATE_SENTINEL" not in caplog.text


def test_expired_deadline_makes_no_provider_requests():
    client = FakePerformanceClient()
    with pytest.raises(NarrativeAdaptationError):
        PerformanceReviewService(client, PerformanceReviewConfig()).build_performance_context(
            "INT. ROOM - DAY #1#\n\nHello.\n",
            SimpleNamespace(metadata={}),
            deadline=0,
            ensure_not_cancelled=lambda: None,
        )
    assert client.windows == []


@pytest.mark.parametrize("splits,styles", [([], ["calm"]), ([7], ["calm", "urgent"])])
def test_note_inside_utterance_is_retained(splits, styles):
    source = "INT. ROOM - DAY #1#\n\n@MINA\nHello.\n[[note\nwith detail]]\nWorld.\n"
    edit = TurnPerformance(scene_number=1, utterance_index=0, split_offsets=splits, styles=styles, insertions=[])
    rendered = apply_performance_review(source, [edit])
    assert "[[note\nwith detail]]\n" in rendered
    turns = FountainParser.parse(rendered).all_utterances()
    assert [u.text for u in turns] == (["Hello. World."] if not splits else ["Hello.", "World."])


def test_adjacent_pauses_across_split_are_rejected():
    source = "INT. ROOM - DAY #1#\n\n@MINA\nHello.<short pause> World.\n"
    edit = TurnPerformance(
        scene_number=1,
        utterance_index=0,
        split_offsets=[19],
        styles=["calm", "urgent"],
        insertions=[InlineCue(offset=19, tag="long pause")],
    )
    with pytest.raises(ValueError, match="adjacent_pause"):
        apply_performance_review(source, [edit])


@pytest.mark.parametrize("speaker", ["MINA", "Narrator"])
def test_dialogue_exclamation_is_not_forced_action(speaker):
    source = f"INT. ROOM - DAY #1#\n\n@{speaker}\n!Stop!\n"
    edit = TurnPerformance(scene_number=1, utterance_index=0, split_offsets=[], styles=['urgent'], insertions=[])
    result = apply_performance_review(source, [edit])
    assert FountainParser.parse(result).all_utterances()[0].text == '!Stop!'


def test_generation_stream_progress_checks_cancellation(monkeypatch):
    from narravant.services.vertex_analysis import CanonicalAnalysis, VertexDocumentAnalyzer
    analyzer = VertexDocumentAnalyzer.__new__(VertexDocumentAnalyzer)
    analyzer.performance_review_max_output_tokens = 4096
    def cancelled():
        raise RuntimeError('synthetic cancellation')
    def collect(**kwargs):
        kwargs['on_progress'](1)
        pytest.fail('cancelled stream continued')
    monkeypatch.setattr(analyzer, '_collect_stream_text', collect)
    with pytest.raises(RuntimeError, match='synthetic cancellation'):
        analyzer.summarize_performance_window('Hello.', [1],
            CanonicalAnalysis(metadata={}, emotion_arc={}, characters=[], turning_points=[]),
            deadline=monotonic() + 10, ensure_not_cancelled=cancelled)


def test_generation_methods_use_frozen_edits_and_shared_deadline(monkeypatch):
    from narravant.services.performance_review import PerformanceContext
    from narravant.services.vertex_analysis import CanonicalAnalysis, CollectedStream, VertexDocumentAnalyzer

    analyzer = VertexDocumentAnalyzer.__new__(VertexDocumentAnalyzer)
    analyzer.performance_review_max_output_tokens = 4096
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        text = (
            '{"scenes":[{"scene_number":1,"intent":"静かな確認"}]}'
            if kwargs["operation"] == "performance_context"
            else '{"edits":[]}'
        )
        return CollectedStream(text, len(text), ("STOP",))

    monkeypatch.setattr(analyzer, "_collect_stream_text", collect)
    analysis = CanonicalAnalysis(
        metadata={"synopsis": "合成あらすじ"}, emotion_arc={}, characters=[], turning_points=[]
    )
    source = "INT. ROOM - DAY #1#\n\n@ミナ\n待つの？来るのね。\n"
    assert analyzer.summarize_performance_window(source, [1], analysis, deadline=12345) == {1: "静かな確認"}
    response = analyzer.review_scene_performance(
        1,
        FountainParser.parse(source).all_utterances(),
        PerformanceContext("合成あらすじ", {1: "静かな確認"}),
        related_turns=[],
        source_cards=[],
        deadline=12345,
        preserve_existing_directions=True,
    )
    assert response.edits == []
    assert all(c["processing_deadline"] == 12345 and c["max_output_tokens"] == 4096 for c in calls)
    assert "candidate_offsets" in calls[1]["contents"]
    assert "120" in calls[1]["contents"]
    schema = calls[1]["config"]["response_json_schema"]
    assert schema["additionalProperties"] is False


def test_review_changes_directions_without_rewriting_dialogue():
    source = "INT. ROOM - DAY #1#\n\n@ミナ\n待つの？来るのね。\n"
    edit = TurnPerformance(
        scene_number=1, utterance_index=0, split_offsets=[4], styles=["不安を抑えて", "ほっとして"], insertions=[]
    )
    result = apply_performance_review(source, [edit])
    turns = FountainParser.parse(result).all_utterances()
    assert [u.text for u in turns] == ["待つの？", "来るのね。"]
    assert "".join(u.text for u in turns) == "待つの？来るのね。"
    assert [u.performance_direction for u in turns] == edit.styles


@pytest.mark.parametrize("offset", [0, 1, 99, -1])
def test_review_rejects_unoffered_offsets(offset):
    source = "INT. ROOM - DAY #1#\n\n@MINA\nHello world.\n"
    edit = TurnPerformance(
        scene_number=1, utterance_index=0, split_offsets=[offset], styles=["calm", "urgent"], insertions=[]
    )
    with pytest.raises(ValueError):
        apply_performance_review(source, [edit])


def test_review_empty_edits_preserve_exact_source():
    source = "\r\nINT. ROOM - DAY #1#\r\n\r\n@MINA\r\nHello world.\r\n"
    assert apply_performance_review(source, []) == source


def test_parenthetical_after_speech_starts_next_performance_turn():
    source = "INT. ROOM - DAY #1#\n\n@ミナ\n(不安を抑えて)\nここで待つの？\n(ほっとして)\nあなたも来るのね。\n"
    turns = FountainParser.parse(source).all_utterances()
    assert [(u.text, u.performance_direction) for u in turns] == [
        ("ここで待つの？", "不安を抑えて"),
        ("あなたも来るのね。", "ほっとして"),
    ]
    assert [u.source_line for u in turns] == [5, 7]


def test_narrator_delivery_is_ephemeral_and_narrator_only():
    data = {
        "source_fountain": "INT. ROOM - DAY #1#\n\n@Narrator\n(urgent)\nHello.\n\n@MINA\nWait.\n",
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voices/synthetic-narrator"},
            {"speaker": "MINA", "voice_id": "voices/synthetic-mina"},
        ],
    }
    request = PlaybackStartRequest.model_validate({"action": "start", "narrator_style": " calm "})
    assert request.narrator_style == "calm"
    plan = build_playback_plan(data, narrator_style=request.narrator_style)
    assert plan.utterances[0].performance_direction == "calm"
    assert "urgent" in plan.utterances[1].performance_direction
    assert "overrides" in plan.utterances[1].performance_direction
    assert plan.utterances[2].performance_direction == ""
    assert "narrator_style" not in data
    assert build_playback_plan(data).utterances[0].performance_direction == ""


@pytest.mark.parametrize("value", [5, [], {}, "x" * 501])
def test_invalid_narrator_delivery(value):
    with pytest.raises(ValueError):
        PlaybackStartRequest.model_validate({"action": "start", "narrator_style": value})
