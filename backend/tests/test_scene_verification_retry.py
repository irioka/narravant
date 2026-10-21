"""場面検証の不正な根拠参照を、本文を漏らさず有限回取り直す。"""

import json

import pytest

from narravant.services.narrative_adaptation import (
    NarrativeAdaptationError,
    PlannedScene,
    ResolvedSourceMaterial,
    SceneContext,
    SourceAnchor,
)
from narravant.services.vertex_analysis import CollectedStream, VertexDocumentAnalyzer


@pytest.fixture
def verification():
    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.verification_max_output_tokens = 4096
    analyzer.verification_max_attempts = 2
    plan = PlannedScene(
        scene_number=3, heading="INT. ROOM - DAY #3#", source_unit_ids=["su1"],
        retained_event_ids=["ev1"], purpose="Keep the key", characters=["MAYA"], causal_notes="A decision",
    )
    anchor = SourceAnchor.text(0, 30)
    context = SceneContext(
        scene_number=3, source_unit_ids=("su1",), retained_events=(),
        source_material=(ResolvedSourceMaterial(source_anchor=anchor, content="PRIVATE_SOURCE_TEXT"),),
    )
    issue = {
        "scene_number": 3, "issue_type": "event_omission", "severity": "major",
        "event_ids": ["ev1"], "source_anchors": [anchor.model_dump()],
        "preservation_condition": "PRIVATE_PRESERVATION", "description": "PRIVATE_DESCRIPTION",
        "revision_direction": "PRIVATE_REVISION",
    }
    return analyzer, plan, context, issue


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("scene_number", 99, "scene_mismatch"),
        ("event_ids", ["PRIVATE_UNKNOWN_EVENT"], "unavailable_event"),
        ("source_anchors", [SourceAnchor.text(900, 999).model_dump()], "unavailable_source_anchor"),
        ("severity", "PRIVATE_INVALID_SEVERITY", "invalid_response_schema"),
    ],
)
def test_invalid_verification_is_retried_without_losing_valid_issues(verification, caplog, field, value, reason):
    analyzer, plan, context, issue = verification
    responses = [json.dumps({"issues": [{**issue, field: value}]}), json.dumps({"issues": [issue]})]
    requests = []

    def collect(**kwargs):
        requests.append(kwargs)
        return CollectedStream(responses.pop(0), 531, ("STOP",))

    analyzer._collect_stream_text = collect
    result = analyzer.verify_scene(plan, "PRIVATE_GENERATED_TEXT", scene_context=context, deadline=999.0)
    assert len(result) == 1 and result[0].severity == "major"
    assert [item["attempt"] for item in requests] == [1, 2]
    assert all(item["processing_deadline"] == 999.0 for item in requests)
    assert reason in caplog.text and "scene_number=3" in caplog.text
    assert "PRIVATE_" not in caplog.text
    assert "allowed_event_ids" in requests[0]["contents"]


@pytest.mark.parametrize("invalid_kind", ["json", "scene", "event", "anchor"])
def test_invalid_verification_exhausts_budget_and_never_passes(verification, caplog, invalid_kind):
    analyzer, plan, context, issue = verification
    changes = {
        "scene": {"scene_number": 99},
        "event": {"event_ids": ["PRIVATE_UNAVAILABLE_EVENT"]},
        "anchor": {"source_anchors": [SourceAnchor.text(900, 999).model_dump()]},
    }
    payload = (
        "PRIVATE_MALFORMED_JSON" if invalid_kind == "json"
        else json.dumps({"issues": [{**issue, **changes[invalid_kind]}]})
    )
    requests = []

    def collect(**kwargs):
        requests.append(kwargs)
        return CollectedStream(payload, 22, ("STOP",))

    analyzer._collect_stream_text = collect
    with pytest.raises(NarrativeAdaptationError) as exc:
        analyzer.verify_scene(plan, "PRIVATE_GENERATED_TEXT", scene_context=context)
    assert exc.value.code == "ADAPTATION_VERIFICATION_FAILED"
    assert len(requests) == 2
    assert "response rejected" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_unexpected_transport_error_does_not_consume_semantic_retries(verification, caplog):
    analyzer, plan, context, _issue = verification
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("PRIVATE_TRANSPORT_FAILURE")

    analyzer._collect_stream_text = collect
    with pytest.raises(RuntimeError):
        analyzer.verify_scene(plan, "PRIVATE_GENERATED_TEXT", scene_context=context)
    assert len(calls) == 1
    assert "PRIVATE_" not in caplog.text


def test_valid_empty_verification_does_not_retry(verification):
    analyzer, plan, context, _issue = verification
    calls = []
    analyzer._collect_stream_text = lambda **kwargs: calls.append(kwargs) or CollectedStream(
        '{"issues": []}', 14, ("STOP",)
    )
    assert analyzer.verify_scene(plan, "PRIVATE_GENERATED_TEXT", scene_context=context) == []
    assert len(calls) == 1
