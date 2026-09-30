"""Synthetic validation tests for the restructured Vertex analysis contract."""

import json

import pytest
from pydantic import ValidationError

from narravant.core.fountain import FountainParser
from narravant.core.valence_vector import default_valence_vectorizer
from narravant.services.vertex_analysis import (
    TURNING_POINT_LABELS,
    GeneratedAnalysis,
    VertexDocumentAnalyzer,
    generated_analysis_json_schema,
)


def make_valid_generated(scene_count: int = 3) -> dict:
    return {
        "metadata": {
            "title": "テスト作品",
            "logline": "あらすじ",
            "synopsis": "概要",
            "theme_setting": "自己受容。根拠は第1、2シーン",
        },
        "valence": [4, 2, 7][:scene_count],
        "tension": [0, 3, -3][:scene_count],
        "characters": [
            {
                "name": "主人公",
                "external_goal": "救出",
                "internal_need": "自立",
                "fear_or_cost": "喪失",
                "obstacle": "敵対者",
                "choice": "退路を断つ",
                "agency": "主体的に選ぶ",
                "goal_to_outcome": "救出願望→共闘の達成",
                "related_turning_points": [3, 5],
                "emotion_arc": [0, 4, 7][:scene_count],
                "voice_traits": "落ち着いた主人公の声",
            }
        ],
        "narrator": {"voice_traits": "落ち着いた語り手の声"},
        "turning_points": [
            {
                "tp_number": tp_number,
                "availability": "identified",
                "scene_number": 1,
                "change": f"物語の変化{tp_number}",
                "involved_characters": [
                    {
                        "name": "主人公",
                        "goal": "救出",
                        "conflict": "包囲",
                        "choice": "突入",
                        "action": "突入した",
                        "change": "覚悟が固まった",
                    }
                ],
                "reason": None,
            }
            for tp_number in range(1, 6)
        ],
    }


def parse_generated(payload: dict) -> GeneratedAnalysis:
    return GeneratedAnalysis.model_validate_json(json.dumps(payload, ensure_ascii=False))


def _parsed_stub():
    fountain = (
        "Title: テスト作品\n\nINT. ROOM - DAY #1#\n\n真理は窓を見る。\n\n"
        "INT. HALL - NIGHT #2#\n\nAction.\n\nINT. ROOM - DAY #3#\n\nAction."
    )
    return FountainParser.parse(fountain)


def test_valid_payload_canonicalizes_with_server_labels():
    generated = parse_generated(make_valid_generated())
    canonical = VertexDocumentAnalyzer._canonicalize(_parsed_stub(), generated, max_points=36)

    assert canonical.turning_points[0]["label"] == TURNING_POINT_LABELS[1]
    assert canonical.turning_points[4]["label"] == TURNING_POINT_LABELS[5]
    assert canonical.emotion_arc["valence_vector"] == default_valence_vectorizer.vectorize(
        canonical.emotion_arc["valence"]
    )
    assert canonical.emotion_arc["characters"] == {"主人公": [0, 4, 7]}
    assert canonical.emotion_arc["scene_mapping"] == [
        {
            "point_number": 1,
            "start_scene_number": 1,
            "end_scene_number": 1,
            "representative_scene_number": 1,
        },
        {
            "point_number": 2,
            "start_scene_number": 2,
            "end_scene_number": 2,
            "representative_scene_number": 2,
        },
        {
            "point_number": 3,
            "start_scene_number": 3,
            "end_scene_number": 3,
            "representative_scene_number": 3,
        },
    ]


def test_canonicalize_adds_spoken_character_omitted_from_main_profiles():
    source = """Title: 全話者

INT. 森 - 昼 #1#

@主人公
進むぞ。

@専門の猟師
ここは危険だ。

INT. 森 - 夜 #2#

地面が暗く沈んだ。

INT. 山 - 朝 #3#

風が吹いた。
"""
    generated = parse_generated(make_valid_generated())

    canonical = VertexDocumentAnalyzer._canonicalize(
        FountainParser.parse(source), generated, max_points=36
    )

    assert [profile["name"] for profile in canonical.characters] == ["主人公", "専門の猟師"]
    omitted = canonical.characters[1]
    assert omitted["voice_traits"] == ""
    assert omitted["external_goal"] is None
    assert canonical.emotion_arc["characters"]["専門の猟師"] == [0, 0, 0]


def test_turning_points_availability_variants_normalized():
    payload = make_valid_generated()
    # Simulate Gemini returning 'available' instead of 'identified'
    for tp in payload["turning_points"]:
        tp["availability"] = "available"
    generated = parse_generated(payload)
    for tp in generated.turning_points:
        assert tp.availability == "identified"


@pytest.mark.parametrize(
    "field, value",
    [
        ("valence", [0, 4, 4]),
        ("valence", [4, 4, 8]),
        ("valence", [4, 4.5, 4]),
        ("tension", [-4, 0, 0]),
        ("tension", [0, 0, 4]),
        ("tension", [0, 0.5, 0]),
    ],
)
def test_out_of_range_scales_are_rejected(field, value):
    payload = make_valid_generated()
    payload[field] = value
    with pytest.raises((ValueError, ValidationError)):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_character_arc_length_must_match_and_zero_means_absence():
    payload = make_valid_generated()
    payload["characters"][0]["emotion_arc"] = [0, 4]
    with pytest.raises((ValueError, ValidationError)):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)

    payload = make_valid_generated()
    payload["characters"][0]["emotion_arc"] = [0, 4, 99]
    with pytest.raises(ValueError, match="character emotion arc scale"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_character_count_must_not_exceed_the_configured_cap():
    """Removing the configured cap from post-parse validation must fail."""
    payload = make_valid_generated()
    payload["characters"].append({**payload["characters"][0], "name": "相棒"})

    with pytest.raises(ValueError, match="character count"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=1)


def test_generated_response_schema_uses_the_runtime_character_cap():
    """Returning an unconstrained characters array in Gemini's schema must fail."""
    schema = generated_analysis_json_schema(max_main_characters=2)

    assert schema["properties"]["characters"]["maxItems"] == 2


def test_analysis_prompt_names_the_runtime_character_cap():
    """Dropping the configured cap from Gemini instructions must fail."""
    prompt = VertexDocumentAnalyzer._analysis_prompt(_parsed_stub(), max_main_characters=2)

    assert "up to 2 retained speaking characters" in prompt


def test_validation_requires_every_retained_speaking_character_profile():
    payload = make_valid_generated()
    with pytest.raises(ValueError, match="character profiles missing"):
        VertexDocumentAnalyzer._validate_generated(
            parse_generated(payload),
            scene_count=3,
            max_main_characters=6,
            required_character_names=["主人公", "専門の猟師"],
        )


def test_validation_requires_non_empty_voice_traits():
    payload = make_valid_generated()
    payload["characters"][0]["voice_traits"] = ""
    with pytest.raises(ValueError, match="character voice traits"):
        VertexDocumentAnalyzer._validate_generated(
            parse_generated(payload), scene_count=3, max_main_characters=6
        )


def test_analysis_prompt_requires_profiles_for_retained_cues():
    parsed = FountainParser.parse(
        "Title: 話者\n\nINT. 森 - 昼 #1#\n\n@専門の猟師\nここは危険だ。"
    )
    prompt = VertexDocumentAnalyzer._analysis_prompt(
        parsed, expected_characters=["専門の猟師"], max_main_characters=6
    )
    assert "専門の猟師" in prompt
    assert "complete profile" in prompt
    assert "Every non-narrator @cue" in prompt


def test_vertex_usage_audit_combines_candidate_and_thought_tokens_only():
    """A total token count includes prompt tokens and cannot enforce the output budget."""
    assert VertexDocumentAnalyzer._combined_output_tokens(20, 4) == 24
    assert VertexDocumentAnalyzer._combined_output_tokens(20, None) is None


def test_turning_points_must_be_exactly_five_in_order():
    payload = make_valid_generated()
    payload["turning_points"] = payload["turning_points"][:4]
    with pytest.raises(ValueError, match="turning points must be exactly five"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_turning_point_scene_number_must_exist():
    payload = make_valid_generated()
    payload["turning_points"][2]["scene_number"] = 99
    with pytest.raises(ValueError, match="turning point scene number"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_related_turning_points_must_reference_existing_tp():
    payload = make_valid_generated()
    payload["characters"][0]["related_turning_points"] = [6]
    with pytest.raises(ValueError, match="related turning points"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_theme_setting_requires_in_range_scene_citation():
    payload = make_valid_generated()
    payload["metadata"]["theme_setting"] = "自己受容。"
    with pytest.raises(ValueError, match="theme setting must cite scene numbers"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)

    payload = make_valid_generated()
    payload["metadata"]["theme_setting"] = "自己受容。根拠は第99シーン"
    with pytest.raises(ValueError, match="existing scene numbers"):
        VertexDocumentAnalyzer._validate_generated(parse_generated(payload), scene_count=3, max_main_characters=6)


def test_correction_reason_covers_new_rules():
    assert VertexDocumentAnalyzer._correction_reason(ValueError("overall valence length")) == "valence_length"
    assert VertexDocumentAnalyzer._correction_reason(ValueError("overall valence scale")) == "valence_scale"
    assert VertexDocumentAnalyzer._correction_reason(ValueError("tension length")) == "tension_length"
    assert VertexDocumentAnalyzer._correction_reason(ValueError("tension scale")) == "tension_scale"
    assert VertexDocumentAnalyzer._correction_reason(ValueError("turning points must be exactly five in order")) == (
        "turning_points"
    )
    assert VertexDocumentAnalyzer._correction_reason(ValueError("related turning points")) == "related_turning_points"
    assert VertexDocumentAnalyzer._correction_reason(ValueError("theme setting must cite scene numbers")) == (
        "theme_citation"
    )


def test_valid_payload_with_not_applicable_tp_and_empty_characters():
    payload = make_valid_generated()
    payload["characters"] = []
    payload["turning_points"][1] = {
        "tp_number": 2,
        "availability": "not_applicable",
        "scene_number": None,
        "change": None,
        "involved_characters": [],
        "reason": "原作内に該当する転換は確認できない。",
    }
    # Scene 1 has TP 1
    payload["turning_points"][0]["availability"] = "identified"
    payload["turning_points"][0]["reason"] = None
    for i in range(2, 5):
        payload["turning_points"][i]["availability"] = "identified"
        payload["turning_points"][i]["reason"] = None

    generated = parse_generated(payload)
    canonical = VertexDocumentAnalyzer._canonicalize(_parsed_stub(), generated, max_points=36)

    assert canonical.characters == []
    assert canonical.turning_points[1]["availability"] == "not_applicable"
    assert canonical.turning_points[1]["label"] == TURNING_POINT_LABELS[2]
    assert canonical.turning_points[1]["scene_number"] is None
    assert canonical.turning_points[1]["change"] is None
    assert canonical.turning_points[1]["involved_characters"] == []
    assert canonical.turning_points[1]["reason"] == "原作内に該当する転換は確認できない。"


sample_char = [
    {
        "name": "A",
        "goal": "g",
        "conflict": "c",
        "choice": "q",
        "action": "a",
        "change": "d",
    }
]


@pytest.mark.parametrize(
    "invalid_tp",
    [
        {
            "tp_number": 2,
            "availability": "not_applicable",
            "scene_number": 1,
            "change": None,
            "involved_characters": [],
            "reason": "理由",
        },
        {
            "tp_number": 2,
            "availability": "not_applicable",
            "scene_number": None,
            "change": "変化",
            "involved_characters": [],
            "reason": "理由",
        },
        {
            "tp_number": 2,
            "availability": "not_applicable",
            "scene_number": None,
            "change": None,
            "involved_characters": sample_char,
            "reason": "理由",
        },
        {
            "tp_number": 2,
            "availability": "not_applicable",
            "scene_number": None,
            "change": None,
            "involved_characters": [],
            "reason": "",
        },
        {
            "tp_number": 2,
            "availability": "identified",
            "scene_number": None,
            "change": "変化",
            "involved_characters": sample_char,
            "reason": None,
        },
        {
            "tp_number": 2,
            "availability": "identified",
            "scene_number": 1,
            "change": "変化",
            "involved_characters": [],
            "reason": None,
        },
        {
            "tp_number": 2,
            "availability": "identified",
            "scene_number": 1,
            "change": "変化",
            "involved_characters": sample_char,
            "reason": "余計な理由",
        },
    ],
)
def test_invalid_turning_point_variants_are_rejected(invalid_tp: dict):
    payload = make_valid_generated()
    for i in range(5):
        payload["turning_points"][i]["availability"] = "identified"
        payload["turning_points"][i]["reason"] = None
    payload["turning_points"][1] = invalid_tp
    with pytest.raises((ValueError, ValidationError)):
        parse_generated(payload)


def test_voice_traits_in_generated_and_canonical_analysis():
    payload = make_valid_generated()
    payload["narrator"] = {"voice_traits": "落ち着いた中低音のナレーション"}
    payload["characters"][0]["voice_traits"] = "力強い熱血漢の声"

    generated = parse_generated(payload)
    assert generated.narrator.voice_traits == "落ち着いた中低音のナレーション"
    assert generated.characters[0].voice_traits == "力強い熱血漢の声"

    canonical = VertexDocumentAnalyzer._canonicalize(_parsed_stub(), generated, max_points=36)
    assert canonical.narrator == {"voice_traits": "落ち着いた中低音のナレーション"}
    assert canonical.characters[0]["voice_traits"] == "力強い熱血漢の声"
