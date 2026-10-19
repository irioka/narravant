"""Synthetic validation tests for the restructured Vertex analysis contract."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from narravant.core.fountain import FountainParser
from narravant.core.valence_vector import default_valence_vectorizer
from narravant.services.narrative_adaptation import (
    CausalEdge,
    CausalPlotGraph,
    CharacterKnowledge,
    SourceAnchor,
    SourceContext,
    SourceUnit,
    StoryEvent,
)
from narravant.services.vertex_analysis import (
    TURNING_POINT_LABELS,
    GeneratedAnalysis,
    IdentifiedSourceTurningPointResult,
    NotApplicableSourceTurningPointResult,
    PlannedSceneResult,
    PlannedScenesResult,
    PlannedStoryPlanResult,
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


def test_analysis_prompt_uses_only_stable_source_character_traits_for_voice_profile():
    from narravant.services.narrative_adaptation import CharacterKnowledge

    source_card = CharacterKnowledge(
        character_name="NOBU",
        evidence_event_ids=["event1"],
        defining_traits=["年齢が明記された老職人。低くかすれた声。"],
        state_by_event={"event1": "この場面では怒っている"},
        knowledge_by_event={"event1": "秘密を知った"},
    )

    prompt = VertexDocumentAnalyzer._analysis_prompt(
        _parsed_stub(),
        expected_characters=["NOBU"],
        max_main_characters=6,
        source_character_cards=[source_card],
    )
    unrelated_prompt = VertexDocumentAnalyzer._analysis_prompt(
        _parsed_stub(),
        expected_characters=["MAYA"],
        max_main_characters=6,
        source_character_cards=[source_card],
    )

    assert "年齢が明記された老職人" in prompt
    assert "この場面では怒っている" not in prompt
    assert "秘密を知った" not in prompt
    assert "Do not assert a concrete age unless it is supported by the source character information" in prompt
    assert "年齢が明記された老職人" not in unrelated_prompt


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


def test_source_plan_requires_five_ordered_turning_point_dispositions() -> None:
    with pytest.raises(ValidationError):
        NotApplicableSourceTurningPointResult(
            tp_number=2,
            label=TURNING_POINT_LABELS[2],
        )

    turning_points = [
        IdentifiedSourceTurningPointResult(
            tp_number=1,
            label=TURNING_POINT_LABELS[1],
            availability="identified",
            event_ids=["ev_1"],
            change="主人公が冒険に踏み出す。",
        ),
        *[
            NotApplicableSourceTurningPointResult(
                tp_number=tp_number,
                label=TURNING_POINT_LABELS[tp_number],
                availability="not_applicable",
                reason="この短い原作には独立した該当イベントがない。",
            )
            for tp_number in range(2, 6)
        ],
    ]
    base = {
        "turning_points": turning_points,
        "important_event_ids": ["ev_1"],
        "central_theme": "好奇心",
        "intended_outcome": "主人公が旅を始める。",
        "presentation_order_event_ids": ["ev_1"],
    }
    assert len(PlannedStoryPlanResult(**base).turning_points) == 5
    with pytest.raises(ValidationError):
        PlannedStoryPlanResult(**{**base, "turning_points": list(reversed(turning_points))})


def test_source_plan_normalizes_known_turning_point_availability_aliases() -> None:
    turning_points = [
        {
            "tp_number": 1,
            "label": TURNING_POINT_LABELS[1],
            "availability": "available",
            "event_ids": ["ev_1"],
            "change": "主人公が行動を起こす。",
        },
        *[
            {
                "tp_number": tp_number,
                "label": TURNING_POINT_LABELS[tp_number],
                "availability": "not_available",
                "reason": "原作に独立した該当イベントがない。",
            }
            for tp_number in range(2, 6)
        ],
    ]

    result = PlannedStoryPlanResult(
        turning_points=turning_points,
        important_event_ids=["ev_1"],
        central_theme="自立",
        intended_outcome="主人公が行動を続ける。",
        presentation_order_event_ids=["ev_1"],
    )

    assert [point.availability for point in result.turning_points] == ["identified"] + [
        "not_applicable"
    ] * 4


def test_plan_schema_requires_evidence_for_identified_turning_points() -> None:
    schema = PlannedScenesResult.model_json_schema()
    turning_point = schema["$defs"]["PlannedStoryPlanResult"]["properties"]["turning_points"]["items"]

    def resolve(reference: dict) -> dict:
        while "$ref" in reference:
            definition_name = reference["$ref"].rsplit("/", 1)[-1]
            reference = schema["$defs"][definition_name]
        return reference

    variants = turning_point.get("oneOf") or turning_point.get("anyOf")
    assert variants is not None
    identified = next(
        resolve(variant)
        for variant in variants
        if resolve(variant)["properties"]["availability"].get("const") == "identified"
        or "identified" in resolve(variant)["properties"]["availability"].get("enum", [])
    )
    not_applicable = next(
        resolve(variant)
        for variant in variants
        if resolve(variant)["properties"]["availability"].get("const") == "not_applicable"
        or "not_applicable" in resolve(variant)["properties"]["availability"].get("enum", [])
    )

    assert {"event_ids", "change"} <= set(identified["required"])
    assert identified["properties"]["event_ids"].get("minItems", 0) >= 1
    assert identified["properties"]["change"].get("minLength", 0) >= 1
    assert "reason" in not_applicable["required"]
    assert not_applicable["properties"]["event_ids"].get("maxItems") == 0


def test_plan_scenes_prompt_receives_source_maps_and_keeps_reader_order() -> None:
    source_context = SourceContext.from_text(
        "abcdefghijkl",
        [SourceUnit(unit_id="su_1", source_anchor=SourceAnchor.text(0, 12))],
    )
    first = StoryEvent(
        event_id="ev_1",
        summary="主人公が扉を見る",
        source_anchor=SourceAnchor.text(0, 3),
        character_states={"主人公": "立ち止まる"},
    )
    second = StoryEvent(
        event_id="ev_2",
        summary="主人公が扉を開ける",
        source_anchor=SourceAnchor.text(7, 10),
        knowledge_changes={"主人公": "奥に人がいると知る"},
    )
    dag = CausalPlotGraph(
        events=[first, second],
        edges=[CausalEdge(from_node_id="ev_2", to_node_id="ev_1", strength="high")],
    )
    character = CharacterKnowledge(
        character_name="主人公",
        evidence_event_ids=["ev_1", "ev_2"],
        state_by_event={"ev_1": "立ち止まる"},
        knowledge_by_event={"ev_2": "奥に人がいると知る"},
    )
    turning_points = [
        {
            "tp_number": 1,
            "label": TURNING_POINT_LABELS[1],
            "availability": "identified",
            "event_ids": ["ev_1"],
            "change": "主人公が進む機会を得る。",
        },
        *[
            {
                "tp_number": tp_number,
                "label": TURNING_POINT_LABELS[tp_number],
                "availability": "not_applicable",
                "reason": "この短い原作には独立した該当イベントがない。",
            }
            for tp_number in range(2, 6)
        ],
    ]
    response = PlannedScenesResult(
        story_plan={
            "turning_points": turning_points,
            "important_event_ids": ["ev_1", "ev_2"],
            "character_motivations": {"主人公": "扉の向こうを知りたい"},
            "central_theme": "好奇心",
            "intended_outcome": "主人公が真実を知る。",
            "presentation_order_event_ids": ["ev_1", "ev_2"],
        },
        character_knowledge=[character],
        scenes=[
            PlannedSceneResult(
                scene_number=1,
                heading="INT. ROOM - DAY #1#",
                source_unit_ids=["su_1"],
                retained_event_ids=["ev_1"],
                purpose="扉に気づく",
                characters=["主人公"],
                causal_notes="主人公は扉を見つける。",
            ),
            PlannedSceneResult(
                scene_number=2,
                heading="INT. ROOM - DAY #2#",
                source_unit_ids=["su_1"],
                retained_event_ids=["ev_2"],
                purpose="扉を開く",
                characters=["主人公"],
                causal_notes="先の場面の後に扉を開く。",
                dependency_scene_numbers=[1],
            ),
        ],
    )
    captured: list[str] = []
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.planning_max_output_tokens = 1024

    def collect(*, contents, **_kwargs):
        captured.append(contents)
        return SimpleNamespace(text=response.model_dump_json())

    analyzer._collect_stream_text = collect
    result = VertexDocumentAnalyzer.plan_scenes(
        analyzer,
        dag,
        source_context,
        [character],
        source_language="ja",
    )

    assert result.story_plan.presentation_order_event_ids == ["ev_1", "ev_2"]
    assert '"unit_id": "su_1"' in captured[0]
    assert '"character_name": "主人公"' in captured[0]
    assert 'CHARACTER NAME LITERALS:\n["主人公"]' in captured[0]
    assert '"knowledge_by_event": {"ev_2": "奥に人がいると知る"}' in captured[0]
    assert '"from_event_id": "ev_2", "to_event_id": "ev_1"' in captured[0]
    assert 'EXPECTED PRESENTATION ORDER EVENT IDS:\n["ev_1", "ev_2"]' in captured[0]
    assert captured[0].index('"event_id": "ev_1"') < captured[0].index('"event_id": "ev_2"')
    required_plan_literals = [
        *[
            f'"tp_number": {number}, "label": "{label}"'
            for number, label in TURNING_POINT_LABELS.items()
        ],
        "#<scene_number>#",
    ]
    missing_plan_literals = [value for value in required_plan_literals if value not in captured[0]]
    assert missing_plan_literals == []


def test_reader_keeps_individual_speaker_identities_for_shared_events() -> None:
    """共同動作・会話の組合せを新しい人物名へ昇格させない。"""
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.reader_max_output_tokens = 1024
    requests = []

    def collect(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(text='{"events": []}')

    analyzer._collect_stream_text = collect
    source = "アヤは旅人に尋ねた。旅人が答え、後でアヤとレンは一緒に礼を言った。"
    analyzer.read_text_window(source, SourceAnchor.text(0, len(source)), source_language="ja")

    prompt = requests[0]["contents"]
    assert "Use one individual character per mapping key" in prompt
    assert "Do not turn a relationship pair or jointly acting people into a character name" in prompt
    assert "Keep unnamed speakers identifiable by their source role" in prompt
    assert "For relationship_changes, key by the individual" in prompt

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


EN = "The river rose, and she knew that her friend must find shelter with the group."
JA = "川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。"


def _fake_analysis_analyzer(monkeypatch, payloads):
    from types import SimpleNamespace

    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.analysis_max_attempts = 2
    analyzer.processing_timeout_seconds = 60
    analyzer.max_main_characters = 6
    analyzer.emotion_arc_max_points = 36
    calls = []
    responses = iter(payloads)

    def collect(**kwargs):
        calls.append(kwargs)
        value = json.dumps(next(responses), ensure_ascii=False)
        return SimpleNamespace(text=value, received_characters=len(value))

    monkeypatch.setattr(analyzer, "_collect_stream_text", collect)
    return analyzer, calls


def _language_payload(description: str) -> dict:
    payload = make_valid_generated(3)
    payload["metadata"].update(
        title="Synthetic storm", logline=description, synopsis=description
    )
    payload["characters"][0]["name"] = "MAYA"
    for point in payload["turning_points"]:
        point["involved_characters"][0]["name"] = "MAYA"
    if description == EN:
        payload["metadata"]["theme_setting"] = "Cooperation. Evidence: Scenes 1, 2."
        for profile in payload["characters"]:
            for key in (
                "external_goal",
                "internal_need",
                "fear_or_cost",
                "obstacle",
                "choice",
                "agency",
                "goal_to_outcome",
                "voice_traits",
            ):
                profile[key] = EN
        payload["narrator"]["voice_traits"] = EN
        for point in payload["turning_points"]:
            point["change"] = EN
            for person in point["involved_characters"]:
                for key in ("goal", "conflict", "choice", "action", "change"):
                    person[key] = EN
    return payload


def test_analysis_retries_wrong_language_within_existing_budget(monkeypatch):
    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [_language_payload(JA), _language_payload(EN)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    result = analyzer.analyze(source, lambda _: None, source_language="en")
    assert result.metadata["synopsis"] == EN
    assert len(calls) == 2
    assert "output_language" in calls[1]["contents"]
    assert calls[0]["processing_deadline"] == calls[1]["processing_deadline"]


def test_analysis_fails_when_all_attempts_wrong_language(monkeypatch):
    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [_language_payload(JA), _language_payload(JA)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    with pytest.raises(
        ValueError,
        match="Vertex returned incomplete structured analysis after configured attempts",
    ):
        analyzer.analyze(source, lambda _: None, source_language="en")
    assert len(calls) == 2


def test_analysis_rejects_single_field_language_mismatch_synopsis(monkeypatch):
    payload = _language_payload(EN)
    payload["metadata"]["synopsis"] = JA
    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [payload, _language_payload(EN)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    result = analyzer.analyze(source, lambda _: None, source_language="en")
    assert result.metadata["synopsis"] == EN
    assert len(calls) == 2


def test_analysis_rejects_single_field_language_mismatch_character(monkeypatch):
    payload = _language_payload(EN)
    payload["characters"][0]["voice_traits"] = JA
    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [payload, _language_payload(EN)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    analyzer.analyze(source, lambda _: None, source_language="en")
    assert len(calls) == 2


def test_analysis_rejects_single_field_language_mismatch_turning_point(monkeypatch):
    payload = _language_payload(EN)
    payload["turning_points"][0]["change"] = JA
    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [payload, _language_payload(EN)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    analyzer.analyze(source, lambda _: None, source_language="en")
    assert len(calls) == 2


def test_analysis_accepts_english_with_japanese_character_and_work_name(monkeypatch):
    payload = _language_payload(EN)
    payload["metadata"]["title"] = "羅生門"
    payload["characters"][0]["name"] = "下人"
    for point in payload["turning_points"]:
        point["involved_characters"][0]["name"] = "下人"
    analyzer, calls = _fake_analysis_analyzer(monkeypatch, [payload])
    source = "Title: 羅生門\n\n" + "\n\n".join(
        f"INT. GATE - DAY #{n}#\n\n@下人\n{EN}" for n in range(1, 4)
    )
    result = analyzer.analyze(source, lambda _: None, source_language="en")
    assert result.metadata["title"] == "羅生門"
    assert result.characters[0]["name"] == "下人"
    assert len(calls) == 1


def test_analysis_rejects_short_japanese_field_for_english_source(monkeypatch):
    """Short Japanese sentences (even under 10 kana) must be rejected for English source."""
    payload_short_jp = _language_payload(EN)
    payload_short_jp["metadata"]["synopsis"] = "川の水が増水し、少女は小屋へ逃げ込んだ。"
    payload_short_jp["characters"][0]["external_goal"] = "家に戻りたい。"

    analyzer, calls = _fake_analysis_analyzer(
        monkeypatch, [payload_short_jp, _language_payload(EN)]
    )
    source = "Title: Synthetic storm\n\n" + "\n\n".join(
        f"INT. CABIN - DAY #{n}#\n\n@MAYA\n{EN}" for n in range(1, 4)
    )
    result = analyzer.analyze(source, lambda _: None, source_language="en")
    assert result.metadata["synopsis"] == EN
    assert len(calls) == 2
    assert "output_language" in calls[1]["contents"]
