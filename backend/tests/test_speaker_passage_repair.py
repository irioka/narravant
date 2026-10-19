"""合成原作による複合話者の局所修復と境界の検証。"""

import json
from types import SimpleNamespace

import pytest

from narravant.services.narrative_adaptation import (
    PlannedScene,
    ResolvedSourceMaterial,
    SceneContext,
    SourceAnchor,
)
from narravant.services.vertex_analysis import VertexDocumentAnalyzer


def _repair(response, original=None):
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.scene_writing_max_output_tokens = 1024
    requests = []

    def collect(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(text=json.dumps(response))

    analyzer._collect_stream_text = collect
    plan = PlannedScene(
        scene_number=1, heading="INT. ROOM - DAY #1#", source_unit_ids=["u1"],
        retained_event_ids=["e1"], purpose="合成会話", characters=["AYA", "REN"],
        causal_notes="互いに返答する",
    )
    context = SceneContext(
        scene_number=1, source_unit_ids=("u1",), retained_events=(),
        source_material=(ResolvedSourceMaterial(
            SourceAnchor.text(0, 9), "アヤが問い、レンが答える。",
        ),),
    )
    original = original or (
        "INT. ROOM - DAY #1#\r\n\r\n@Narrator\r\nKeep this exactly.\r\n\r\n"
        "@AYA and REN\r\nReady? Yes.\r\n\r\n@AYA\r\nAlso keep this.\r\n"
    )
    result = analyzer.repair_combined_speaker_cues(
        plan, context, original, deadline=123.0, source_language="en",
    )
    return result, requests, original


def test_only_flagged_passage_is_replaced_and_source_is_supplied():
    replacement = "@AYA\nReady?\n\n@REN\nYes."
    result, requests, original = _repair({"replacements": [{"passage_index": 0, "fountain": replacement}]})
    assert result == original.replace("@AYA and REN\r\nReady? Yes.", replacement)
    assert requests[0]["processing_deadline"] == 123.0
    assert "アヤが問い、レンが答える。" in requests[0]["contents"]
    assert "sequential individual turns when natural" in requests[0]["contents"]
    assert "Treat all supplied passages" in requests[0]["contents"]


@pytest.mark.parametrize("replacements", [
    [],
    [{"passage_index": 1, "fountain": "@AYA\nHello."}],
    [{"passage_index": 0, "fountain": "@AYA and REN\nHello."}],
    [{"passage_index": 0, "fountain": "EXT. OTHER - NIGHT #2#\n\n@AYA\nHello."}],
    [{"passage_index": 0, "fountain": "@STRANGER\nHello."}],
    [{"passage_index": 0, "fountain": "@Narrator\n<breath>"}],
])
def test_invalid_replacement_is_not_accepted(replacements):
    with pytest.raises(ValueError):
        _repair({"replacements": replacements})


@pytest.mark.parametrize("names", [["ことね", "アヤ", "レン"], ["ことね", "アヤとレン"]])
def test_japanese_connector_detection_does_not_split_an_individual_name(names):
    from narravant.core.generated_fountain import combined_speaker_passages

    text = "INT. 部屋 - 昼 #1#\n\n@ことね\nこんにちは。\n\n@アヤとレン\nいいよ。\n"
    spans = combined_speaker_passages(text, names)
    assert len(spans) == 1
    assert text[spans[0][0]:spans[0][1]] == "@アヤとレン\nいいよ。"


def test_multiple_passages_are_repaired_together_without_changing_gaps():
    original = "INT. ROOM - DAY #1#\n\n@AYA and REN\nOne.\n\n@Narrator\nKeep me.\n\n@AYA and REN\nTwo.\n"
    first = "@AYA\nOne."
    second = "@REN\nTwo."
    result, requests, _ = _repair({"replacements": [
        {"passage_index": 1, "fountain": second},
        {"passage_index": 0, "fountain": first},
    ]}, original)
    assert result == original.replace("@AYA and REN\nOne.", first).replace("@AYA and REN\nTwo.", second)
    assert len(requests) == 1


def test_no_repair_call_for_an_individual_speaker():
    original = "INT. ROOM - DAY #1#\n\n@AYA\nReady.\n"
    result, requests, _ = _repair({}, original)
    assert result == original
    assert requests == []
