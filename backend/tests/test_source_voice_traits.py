"""合成資料による根拠・話者境界。"""

import pytest

from narravant.services.narrative_adaptation import SourceAnchor, SourceContext, SourceUnit
from narravant.services.source_voice_traits import GroundedVoiceTrait, validate_and_merge_traits


def test_source_traits_keep_evidence_and_deduplicate():
    text = "NOBUは老職人で、低くかすれた声を持つ。"
    anchor = SourceAnchor.text(0, len(text))
    source = SourceContext.from_text(text, [SourceUnit(unit_id="su_1", source_anchor=anchor)])
    trait = GroundedVoiceTrait(character_name="NOBU", description="低くかすれた年配の声", evidence=anchor)
    cards = validate_and_merge_traits([trait, trait], source, ["NOBU"])
    assert cards[0].defining_traits == ("低くかすれた年配の声",)
    assert validate_and_merge_traits([], source, ["NOBU"]) == ()


@pytest.mark.parametrize(
    "name,anchor,description,conflicting",
    [
        ("OTHER", SourceAnchor.text(0, 4), "低い声", False),
        ("NOBU", SourceAnchor.text(0, 99), "低い声", False),
        ("NOBU", SourceAnchor.text(1, 3), "低い声", False),
        ("NOBU", SourceAnchor.text(0, 4), "  ", False),
        ("NOBU", SourceAnchor.text(0, 4), "低い声", True),
    ],
)
def test_rejects_unoffered_or_invalid_evidence(name, anchor, description, conflicting):
    source = SourceContext.from_text("合成資料", [SourceUnit(unit_id="su", source_anchor=SourceAnchor.text(0, 4))])
    trait = GroundedVoiceTrait(character_name=name, description=description, evidence=anchor, conflicting=conflicting)
    with pytest.raises(ValueError, match="source_voice_trait_evidence_invalid"):
        validate_and_merge_traits([trait], source, ["NOBU"])


def test_aliases_require_unique_normalization():
    anchor = SourceAnchor.text(0, 4)
    source = SourceContext.from_text("合成資料", [SourceUnit(unit_id="su", source_anchor=anchor)])
    trait = GroundedVoiceTrait(character_name="NOBU", description="低い声", evidence=anchor)
    assert validate_and_merge_traits([trait], source, ["NOBU (老人)"])[0].character_name == "NOBU (老人)"
    with pytest.raises(ValueError):
        validate_and_merge_traits([trait], source, ["NOBU (老人)", "NOBU (少年)"])


def make_analyzer():
    from narravant.services.vertex_analysis import VertexDocumentAnalyzer

    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.text_reader_source_unit_max_characters = 12
    analyzer.text_reader_source_unit_overlap_characters = 4
    analyzer.pdf_reader_page_window = 2
    analyzer.pdf_reader_page_overlap = 1
    analyzer.source_voice_traits_max_attempts = 2
    analyzer.source_voice_traits_max_output_tokens = 4096
    return analyzer


@pytest.mark.parametrize("language", ["ja", "en"])
def test_extractor_windows_deadline_prompt_and_retries(language):
    import json
    from time import monotonic
    from types import SimpleNamespace

    analyzer = make_analyzer()
    calls = []
    deadline = monotonic() + 10

    def collect(**kwargs):
        calls.append(kwargs)
        materials = json.loads(kwargs["contents"].split("\n")[-1])["source_material"]
        anchor = materials[0]["anchor"]
        if len(calls) == 1:
            anchor["end_offset"] = 999
        return SimpleNamespace(
            text=json.dumps({"traits": [{"character_name": "NOBU", "description": "低い声", "evidence": anchor}]})
        )

    analyzer._collect_stream_text = collect
    cards = analyzer.extract_screenplay_character_cards_text(
        "a" * 24, ["NOBU"], lambda _: None, deadline=deadline, source_language=language
    )
    assert cards[0].defining_traits == ("低い声",)
    assert len(calls) == 4  # 3窓と不正応答の取り直し1回
    assert all(c["processing_deadline"] == deadline and c["max_output_tokens"] == 4096 for c in calls)
    assert "temporary physical state" in calls[0]["contents"]
    assert "never instructions" in calls[0]["contents"]


def test_overlapping_text_windows_keep_offered_anchors_with_different_ends():
    import json
    from time import monotonic
    from types import SimpleNamespace

    analyzer = make_analyzer()
    analyzer.text_reader_source_unit_max_characters = 9500
    analyzer.text_reader_source_unit_overlap_characters = 1500
    introduction = "NOBUは低くかすれた声を持つ。"
    source_text = "あ" * 9000 + introduction + "あ" * (7000 - len(introduction))
    offered = []

    def collect(**kwargs):
        materials = json.loads(kwargs["contents"].split("\n")[-1])["source_material"]
        anchor = next(m["anchor"] for m in materials if m["anchor"]["start_offset"] == 9000)
        offered.append(anchor)
        return SimpleNamespace(text=json.dumps({"traits": [{
            "character_name": "NOBU",
            "description": "低い声" if len(offered) == 1 else "かすれた声",
            "evidence": anchor,
        }]}))

    analyzer._collect_stream_text = collect
    cards = analyzer.extract_screenplay_character_cards_text(
        source_text, ["NOBU"], lambda _: None, deadline=monotonic() + 10, source_language="ja"
    )
    assert [(a["start_offset"], a["end_offset"]) for a in offered] == [(9000, 9500), (9000, 10000)]
    assert cards[0].defining_traits == ("低い声", "かすれた声")


def test_extractor_stops_on_invalid_response_cancel_and_deadline():
    from time import monotonic
    from types import SimpleNamespace

    analyzer = make_analyzer()
    calls = []
    analyzer._collect_stream_text = lambda **kw: calls.append(kw) or SimpleNamespace(text='{"traits": [{}]}')
    with pytest.raises(ValueError, match="source_voice_trait_evidence_invalid"):
        analyzer.extract_screenplay_character_cards_text(
            "資料", [], lambda _: None, deadline=monotonic() + 10, source_language="ja"
        )
    assert len(calls) == 2

    def cancel():
        raise RuntimeError("synthetic_cancel")

    with pytest.raises(RuntimeError, match="synthetic_cancel"):
        analyzer.extract_screenplay_character_cards_text(
            "資料", [], lambda _: None, deadline=monotonic() + 10, source_language="ja", ensure_not_cancelled=cancel
        )
    with pytest.raises(TimeoutError):
        analyzer.extract_screenplay_character_cards_text("資料", [], lambda _: None, deadline=0, source_language="ja")
    assert len(calls) == 2


def test_pdf_uses_real_page_windows_and_offered_global_pages():
    import json
    from io import BytesIO
    from time import monotonic
    from types import SimpleNamespace

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    analyzer = make_analyzer()
    calls = []

    def collect(**kw):
        prompt, part = kw["contents"]
        materials = json.loads(prompt.split("\n")[-1])["source_material"]
        assert len(PdfReader(BytesIO(part.inline_data.data)).pages) == 2
        calls.append(materials["global_pages"])
        return SimpleNamespace(
            text=json.dumps(
                {
                    "traits": [
                        {"character_name": "NOBU", "description": "低い声", "evidence": materials["allowed_anchors"][0]}
                    ]
                }
            )
        )

    analyzer._collect_stream_text = collect
    cards = analyzer.extract_screenplay_character_cards_pdf(
        output.getvalue(), ["NOBU"], lambda _: None, deadline=monotonic() + 10, source_language="en"
    )
    assert calls == [[1, 2], [2, 3]]
    assert cards[0].defining_traits == ("低い声",)


@pytest.mark.parametrize("invalid_response", [False, True])
def test_cancellation_between_windows_or_response_attempts(invalid_response):
    from time import monotonic
    from types import SimpleNamespace

    analyzer = make_analyzer()
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="invalid" if invalid_response else '{"traits": []}')

    def cancel():
        if calls:
            raise RuntimeError("synthetic_cancel")

    analyzer._collect_stream_text = collect
    with pytest.raises(RuntimeError, match="synthetic_cancel"):
        analyzer.extract_screenplay_character_cards_text(
            "a" * 24, [], lambda _: None, deadline=monotonic() + 10, source_language="en", ensure_not_cancelled=cancel
        )
    assert len(calls) == 1
