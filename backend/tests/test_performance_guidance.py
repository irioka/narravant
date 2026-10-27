"""公式タグの全経路と、抽象的な演技意図を優先する生成契約を検査する。"""

import re
from pathlib import Path

import pytest

from narravant.core.fountain import FountainParser
from narravant.core.speech_notation import parse_speech_notation, spoken_text_by_speaker
from narravant.services.performance_review import InlineCue, TurnPerformance, apply_performance_review
from narravant.services.playback import build_playback_plan
from narravant.services.speech_segments import build_speech_segments
from narravant.services.tts import build_tts_request

# Google公式GenerateContent TTSガイドの2026-10-26時点の全表記。同義表記も保持する。
DOCUMENTED_TAGS = (
    "argh",
    "breath",
    "heavy breath",
    "exhales",
    "cackle",
    "cheer",
    "chuckle",
    "chuckles",
    "cough",
    "cry",
    "gasp",
    "giggle",
    "groan",
    "growl",
    "grunt",
    "grr",
    "hiss",
    "laugh",
    "laughter",
    "moan",
    "pant",
    "pff",
    "phew",
    "scream",
    "shout",
    "shriek",
    "sigh",
    "sighs",
    "sneeze",
    "snicker",
    "snort",
    "sob",
    "throat-clearing",
    "tsk",
    "whimper",
    "whispers",
    "whispering",
    "yawn",
    "short pause",
    "long pause",
)


def test_schema_matches_all_documented_tag_spellings():
    enum = InlineCue.model_json_schema()["properties"]["tag"]["enum"]
    assert set(enum) == set(DOCUMENTED_TAGS)


@pytest.mark.parametrize("tag", DOCUMENTED_TAGS)
def test_supported_tag_survives_review_parser_segmentation_and_tts_payload(tag):
    source = "INT. ROOM - DAY #1#\n\n@MINA\nHello.\n"
    edit = TurnPerformance(
        scene_number=1,
        utterance_index=0,
        split_offsets=[],
        styles=["心から安堵して"],
        insertions=[InlineCue(offset=0, tag=tag)],
    )
    rendered = apply_performance_review(source, [edit])
    utterance = FountainParser.parse(rendered).all_utterances()[0]
    notation = parse_speech_notation(utterance.text)
    assert spoken_text_by_speaker("MINA", notation) == {"MINA": "Hello."}
    assert notation.tokens[0].kind == ("pause" if tag in {"short pause", "long pause"} else "vocal")
    plan = build_playback_plan(
        {
            "source_fountain": rendered,
            "voice_assignments": [
                {"speaker": "MINA", "voice_id": "voice_synthetic_mina"},
                {"speaker": "Narrator", "voice_id": "voice_synthetic_narrator"},
            ],
        },
        start_utterance_index=1,
    )
    segments = build_speech_segments(plan.utterances[0])
    assert len(segments) == 1
    part = build_tts_request(segments[0], "gemini-3.8-flash-tts", "en-US")["contents"][0]["parts"][0]
    assert part == {"text": f"<{tag}>Hello.", "speechMetadata": {"style": "心から安堵して"}}


@pytest.mark.parametrize("tag", ["applause", "music", "footsteps", "unknown"])
def test_non_vocal_and_undocumented_tags_are_not_auto_generated(tag):
    with pytest.raises(ValueError):
        InlineCue(offset=0, tag=tag)


def test_frontend_and_backend_share_the_documented_tag_set():
    path = Path(__file__).resolve().parents[2] / "frontend/src/features/analysis/fountain-scenes.ts"
    if not path.exists():
        pytest.skip("frontendのない配布物")
    match = re.search(r"const SPEECH_TAGS = /<\(\?:(.*?)\)>/g", path.read_text())
    assert match is not None
    assert set(match.group(1).split("|")) == set(DOCUMENTED_TAGS)


def test_conversion_and_review_share_abstract_first_policy_and_full_tag_rules(monkeypatch):
    from narravant.services.performance_guidance import TAG_SELECTION_RULES, build_performance_guidance
    from narravant.services.performance_review import PerformanceContext
    from narravant.services.vertex_analysis import FOUNTAIN_PERFORMANCE_GUIDE, CollectedStream, VertexDocumentAnalyzer

    policy = build_performance_guidance()
    classified = [tag for tags, _ in TAG_SELECTION_RULES for tag in tags]
    assert sorted(classified) == sorted(DOCUMENTED_TAGS)
    assert "ABSTRACT INTENT FIRST" in policy
    assert "CONCRETE DELIVERY EXCEPTIONS" in policy
    assert "SOURCE EVIDENCE BEFORE INSERTION" in policy
    assert "NEVER require a tag count or variety quota" in policy
    assert all(f"<{tag}>" in policy for tag in DOCUMENTED_TAGS)
    assert policy in FOUNTAIN_PERFORMANCE_GUIDE
    assert "Return only the requested structured edits" not in FOUNTAIN_PERFORMANCE_GUIDE
    assert "Frozen-text review must preserve" not in FOUNTAIN_PERFORMANCE_GUIDE
    client = VertexDocumentAnalyzer.__new__(VertexDocumentAnalyzer)
    client.performance_review_max_output_tokens = 4096
    captured = []

    def collect(**kwargs):
        captured.append(kwargs)
        return CollectedStream('{"edits":[]}', 12, ("STOP",))

    monkeypatch.setattr(client, "_collect_stream_text", collect)
    client.review_scene_performance(
        1,
        FountainParser.parse("INT. ROOM - DAY #1#\n\n@MINA\nHello.").all_utterances(),
        PerformanceContext("A synthetic scene", {1: "reassurance"}),
        related_turns=[],
        source_cards=[],
        deadline=12345,
        preserve_existing_directions=False,
    )
    assert policy in captured[0]["contents"]
    schema = captured[0]["config"]["response_json_schema"]
    assert set(schema["$defs"]["InlineCue"]["properties"]["tag"]["enum"]) == set(DOCUMENTED_TAGS)


def test_renderer_does_not_duplicate_existing_cue():
    edit = TurnPerformance(
        scene_number=1, utterance_index=0, split_offsets=[], styles=["心から安堵して"], insertions=[]
    )
    result = apply_performance_review("INT. ROOM - DAY #1#\n\n@MINA\nHello.\n", [edit])
    assert result.count("@MINA") == 1


def test_composed_delivery_keeps_local_priority_without_voice_maintenance_commands():
    from narravant.services.voice_prompt_guidance import compose_delivery

    style = compose_delivery("落ち着いて", "相手を安心させるように")
    assert "落ち着いて" in style
    assert style.endswith("相手を安心させるように")
    assert "overrides default pace" in style
    assert "vocal identity" not in style
