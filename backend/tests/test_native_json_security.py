"""Native JSON is an external trust boundary, tested with synthetic fixtures only."""

from __future__ import annotations

import json

import pytest

from narravant.services.ingestion import (
    ImportValidationError,
    ValidatedImport,
    native_exchange_to_v1,
)


def _native_payload() -> dict[str, object]:
    character = {
        "name": "A",
        "external_goal": "Goal",
        "internal_need": "Need",
        "fear_or_cost": "Cost",
        "obstacle": "Obstacle",
        "choice": "Choice",
        "agency": "Agency",
        "goal_to_outcome": "Outcome",
        "related_turning_points": [1, 3],
        "voice_traits": "落ち着いた低音",
    }
    return {
        "format": "narravant-native",
        "format_version": 1,
        "exported_at": "2026-09-12T12:34:56Z",
        "document": {
            "title": "Native",
            "source_fountain": "Title: Native\n\nINT. ROOM - DAY #1#\n\nAction.\n",
            "metadata": {
                "title": "Native",
                "logline": "Logline",
                "synopsis": "Synopsis",
                "theme_setting": "Theme",
            },
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": number,
                        "availability": "identified",
                        "scene_number": 1,
                        "change": f"Change {number}",
                        "involved_characters": [
                            {
                                "name": "A",
                                "goal": "g",
                                "conflict": "c",
                                "choice": "q",
                                "action": "a",
                                "change": "d",
                            }
                        ],
                        "reason": None,
                    }
                    for number in range(1, 6)
                ],
                "characters": [character],
            },
            "emotion_arc": {"valence": [4], "tension": [0], "characters": {"A": [4]}},
            "narrator": {"voice_traits": "知的な語り手"},
            "voice_assignments": [
                {"speaker": "A", "voice_id": "voices/voice-a", "voice_traits": "落ち着いた低音"},
                {"speaker": "ナレーター", "voice_id": "voices/voice-narrator", "voice_traits": "知的な語り手"},
            ],
        },
    }


def _imported() -> ValidatedImport:
    return ValidatedImport("native.json", ".json", "application/json", None)


def test_native_json_recomputes_server_fields_and_never_accepts_client_identity() -> None:
    structured = native_exchange_to_v1(
        json.dumps(_native_payload()), "server-doc", "server-user", _imported(), max_points=36
    )

    assert structured["document_id"] == "server-doc"
    assert structured["owner_user_id"] == "server-user"
    assert structured["scenes"][0]["scene_number"] == 1  # type: ignore[index]
    assert structured["analysis"]["turning_points"][0]["label"] == "Opportunity"  # type: ignore[index]
    assert structured["analysis"]["characters"][0]["voice_traits"] == "落ち着いた低音"  # type: ignore[index]
    assert structured["narrator"] == {"voice_traits": "知的な語り手"}  # type: ignore[index]
    assert structured["voice_assignments"] == [  # type: ignore[index]
        {"speaker": "A", "voice_id": "voices/voice-a", "voice_traits": "落ち着いた低音"},
        {"speaker": "ナレーター", "voice_id": "voices/voice-narrator", "voice_traits": "知的な語り手"},
    ]
    assert len(structured["emotion_arc"]["valence_vector"]) == 10  # type: ignore[index]


def test_native_json_uses_36_points_and_regenerates_mapping_for_37_scenes() -> None:
    """Requiring Native arcs to equal source-scene count must fail this test."""
    payload = _native_payload()
    scenes = "\n\n".join(f"INT. ROOM - DAY #{number}#\n\nAction {number}." for number in range(1, 38))
    payload["document"]["source_fountain"] = f"Title: Native\n\n{scenes}\n"  # type: ignore[index]
    payload["document"]["emotion_arc"] = {  # type: ignore[index]
        "valence": [4] * 36,
        "tension": [0] * 36,
        "characters": {"A": [4] * 36},
    }
    structured = native_exchange_to_v1(
        json.dumps(payload),
        "server-doc",
        "server-user",
        _imported(),
        max_points=36,
    )

    mapping = structured["emotion_arc"]["scene_mapping"]  # type: ignore[index]
    assert len(mapping) == 36
    assert mapping[-1] == {  # type: ignore[index]
        "point_number": 36,
        "start_scene_number": 36,
        "end_scene_number": 37,
        "representative_scene_number": 36,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["document"].__setitem__("document_id", "attacker"),  # type: ignore[index]
        lambda payload: payload["document"].__setitem__("valence_vector", [0.0] * 10),  # type: ignore[index]
        lambda payload: payload["document"]["metadata"].__setitem__("unexpected", True),  # type: ignore[index]
        lambda payload: payload["document"]["narrator"].__setitem__("extra", "malicious"),  # type: ignore[index]
        lambda payload: payload["document"]["voice_assignments"][0].__setitem__("extra", "malicious"),  # type: ignore[index]
        lambda payload: payload["document"]["analysis"]["characters"][0].__setitem__("extra", "malicious"),  # type: ignore[index]
    ],
)
def test_native_json_rejects_unknown_or_tampered_fields(mutate) -> None:
    payload = _native_payload()
    mutate(payload)

    with pytest.raises(ImportValidationError, match="Native JSON"):
        native_exchange_to_v1(json.dumps(payload), "server-doc", "server-user", _imported(), max_points=36)


def test_native_json_rejects_duplicate_keys_and_title_mismatch() -> None:
    duplicate = '{"format":"narravant-native","format":"narravant-native"}'
    with pytest.raises(ImportValidationError, match="Native JSON"):
        native_exchange_to_v1(duplicate, "server-doc", "server-user", _imported(), max_points=36)

    payload = _native_payload()
    payload["document"]["metadata"]["title"] = "Different"  # type: ignore[index]
    with pytest.raises(ImportValidationError, match="タイトル"):
        native_exchange_to_v1(json.dumps(payload), "server-doc", "server-user", _imported(), max_points=36)


def test_native_json_accepts_not_applicable_tp_and_empty_characters() -> None:
    payload = _native_payload()
    payload["document"]["analysis"]["characters"] = []  # type: ignore[index]
    payload["document"]["emotion_arc"]["characters"] = {}  # type: ignore[index]
    # TP 2 is not_applicable, TP 1 has a character "Supporting" not in Main Characters
    for tp in payload["document"]["analysis"]["turning_points"]:  # type: ignore[union-attr]
        tp["availability"] = "identified"
        tp["reason"] = None
    payload["document"]["analysis"]["turning_points"][0]["involved_characters"] = [  # type: ignore[index]
        {
            "name": "Supporting",
            "goal": "g",
            "conflict": "c",
            "choice": "q",
            "action": "a",
            "change": "d",
        }
    ]
    payload["document"]["analysis"]["turning_points"][1] = {  # type: ignore[index]
        "tp_number": 2,
        "availability": "not_applicable",
        "scene_number": None,
        "change": None,
        "involved_characters": [],
        "reason": "原作内に該当する転換は確認できない。",
    }

    structured = native_exchange_to_v1(json.dumps(payload), "server-doc", "server-user", _imported(), max_points=36)

    assert structured["analysis"]["characters"] == []
    tps = structured["analysis"]["turning_points"]
    assert tps[0]["label"] == "Opportunity"
    assert tps[0]["involved_characters"][0]["name"] == "Supporting"
    assert tps[1]["availability"] == "not_applicable"
    assert tps[1]["label"] == "Change of Plans"
    assert tps[1]["reason"] == "原作内に該当する転換は確認できない。"
    assert tps[1]["scene_number"] is None


def test_native_json_rejects_tp_with_label_or_extra_field() -> None:
    payload = _native_payload()
    for tp in payload["document"]["analysis"]["turning_points"]:  # type: ignore[union-attr]
        tp["availability"] = "identified"
        tp["reason"] = None
    payload["document"]["analysis"]["turning_points"][0]["label"] = "Opportunity"  # type: ignore[index]

    with pytest.raises(ImportValidationError, match="Native JSON"):
        native_exchange_to_v1(json.dumps(payload), "server-doc", "server-user", _imported(), max_points=36)
