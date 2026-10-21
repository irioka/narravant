"""脚色プロンプトと脚本構造の整合性検証。"""

from __future__ import annotations

from narravant.core.fountain import FountainParser
from narravant.db.database import DatabaseManager, DocumentRepository, TaskRepository
from narravant.services.ingestion import (
    ImportDraftStore,
    ImportService,
    validate_import,
)
from narravant.services.narrative_adaptation import SourceKind
from narravant.services.tasks import EphemeralTaskEvents, TaskManager
from narravant.services.vertex_analysis import (
    CONVERSION_PROMPT,
    CanonicalAnalysis,
    VertexDocumentAnalyzer,
    normalize_single_conversion_scene_numbers,
)
from narravant.storage.gcs import InMemoryScriptStorageClient


class StubEmptyAnalyzer:
    """Analyzer that returns empty script or script with scene headings but no utterances."""

    def __init__(self, fountain: str) -> None:
        self.fountain = fountain

    def normalize_screenplay_text(self, *_args, **_kwargs) -> str:
        return self.fountain

    def normalize_screenplay_pdf(self, *_args, **_kwargs) -> str:
        return self.fountain

    def analyze(self, *_args, **_kwargs) -> CanonicalAnalysis:
        return CanonicalAnalysis(
            metadata={"title": "Empty", "logline": "Log", "synopsis": "Syn", "theme_setting": "第1シーン"},
            emotion_arc={"valence": [4], "tension": [0], "characters": {}},
            characters=[],
            turning_points=[],
        )


class StubAdapter:
    def classify_text(self, *_args, **_kwargs) -> SourceKind:
        return SourceKind.SCREENPLAY

    def classify_pdf(self, *_args, **_kwargs) -> SourceKind:
        return SourceKind.SCREENPLAY


def _create_service(analyzer: object) -> tuple[ImportService, TaskManager]:
    db = DatabaseManager(":memory:")
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user("owner-1", "owner@example.test", "Owner")
    storage = InMemoryScriptStorageClient("test-bucket")
    tasks = TaskManager(TaskRepository(db), ephemeral_events=EphemeralTaskEvents())
    service = ImportService(
        repo,
        storage,
        tasks,
        ImportDraftStore(3600),
        analyzer,  # type: ignore[arg-type]
        StubAdapter(),  # type: ignore[arg-type]
        processing_timeout_seconds=60,
        max_size_bytes=1024 * 1024,
        pdf_max_pages=100,
        fdx_max_depth=32,
        txt_minimum_confidence=0.70,
        emotion_arc_max_points=36,
    )
    return service, tasks


def test_conversion_prompt_relaxes_creation_constraint() -> None:
    """CONVERSION_PROMPT must allow audiobook-friendly creative adaptation."""
    assert "must not invent events, characters" not in CONVERSION_PROMPT
    assert "audiobook" in CONVERSION_PROMPT.lower() or "audio" in CONVERSION_PROMPT.lower()


def test_conversion_prompt_requires_contextual_original_performance_direction() -> None:
    """Directions should be selective and distinguish sustained acting from momentary tags."""
    assert "immediate situation" in CONVERSION_PROMPT
    assert "dialogue's wording" in CONVERSION_PROMPT
    assert "Do not merely copy" in CONVERSION_PROMPT
    assert "Parentheticals are optional" in CONVERSION_PROMPT
    assert "Do not add the same direction to every turn" in CONVERSION_PROMPT
    assert "Do not add pause tags at every punctuation mark" in CONVERSION_PROMPT
    assert "<short pause>" in CONVERSION_PROMPT
    assert "<breath>" in CONVERSION_PROMPT
    assert "[[listener:話者名]]" in CONVERSION_PROMPT


def test_screenplay_normalization_prompt_uses_shared_d8_performance_rules() -> None:
    analyzer = object.__new__(VertexDocumentAnalyzer)
    captured = {}

    def convert(**kwargs):
        captured.update(kwargs)
        return "Title: Synthetic\\n\\n=== NARRAVANT FOUNTAIN COMPLETE ==="

    analyzer._convert_with_continuation = convert
    analyzer.normalize_screenplay_text(
        "Title: Synthetic\\n\\nINT. ROOM - DAY #1#\\n\\n@Narrator\\nA calm line.",
        source_language="en",
    )
    text_prompt = captured["initial_contents"]
    analyzer.normalize_screenplay_pdf(b"synthetic pdf bytes")
    pdf_prompt = captured["initial_contents"][0]

    for prompt in (text_prompt, pdf_prompt):
        assert "Parentheticals are optional" in prompt
        assert "Do not add the same direction to every turn" in prompt
        assert "Do not duplicate an existing parenthetical or narrator cue" in prompt
        assert "Do not add pause tags at every punctuation mark" in prompt
        assert "[[listener:話者名]]" in prompt


def test_scene_verification_returns_structured_rooted_issues() -> None:
    import json

    from narravant.services.narrative_adaptation import (
        PlannedScene,
        ResolvedSourceMaterial,
        SceneContext,
        SourceAnchor,
    )
    from narravant.services.vertex_analysis import CollectedStream

    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.verification_max_output_tokens = 1000
    analyzer.verification_max_attempts = 3
    requests = []
    response = {
        "issues": [
            {
                "scene_number": 1,
                "issue_type": "event_omission",
                "severity": "major",
                "event_ids": ["event1"],
                "source_anchors": [
                    {"kind": "text", "start_offset": 0, "end_offset": 20},
                ],
                "preservation_condition": "Retain Maya's decision to keep the key.",
                "description": "The generated scene omits her decision.",
                "revision_direction": "Restore the decision without changing the heading.",
            }
        ]
    }

    def collect(**kwargs):
        requests.append(kwargs)
        return CollectedStream(json.dumps(response), 30, ("STOP",))

    analyzer._collect_stream_text = collect
    plan = PlannedScene(
        scene_number=1,
        heading="INT. ROOM - DAY #1#",
        source_unit_ids=["unit1"],
        retained_event_ids=["event1"],
        omitted_event_ids=[],
        purpose="Maya decides what to do with the key.",
        characters=["MAYA"],
        causal_notes="The key is important.",
    )
    context = SceneContext(
        scene_number=1,
        source_unit_ids=("unit1",),
        retained_events=(),
        source_material=(
            ResolvedSourceMaterial(
                source_anchor=SourceAnchor.text(0, 20),
                content="Maya keeps the brass key.",
            ),
        ),
    )

    issues = analyzer.verify_scene(
        plan,
        "INT. ROOM - DAY #1#\\n\\n@MAYA\\nI will keep it.",
        scene_context=context,
        analysis_context={"metadata": {"title": "Synthetic analysis"}},
    )

    assert len(issues) == 1
    assert issues[0].event_ids == ["event1"]
    assert "Maya keeps the brass key." in requests[0]["contents"]
    assert "Synthetic analysis" in requests[0]["contents"]
    assert "Do not reject stylistic preference without source evidence" in requests[0]["contents"]
    assert "Check each line against the actual source speaker" in requests[0]["contents"]
    assert "A combined cue is not evidence that both people spoke" in requests[0]["contents"]
    assert "misattribution as speaker_attribution" in requests[0]["contents"]


def test_scene_reviser_receives_draft_source_and_all_quality_issues() -> None:
    from narravant.services.narrative_adaptation import (
        PlannedScene,
        QualityIssue,
        ResolvedSourceMaterial,
        SceneContext,
        SourceAnchor,
    )
    from narravant.services.vertex_analysis import CollectedStream

    analyzer = object.__new__(VertexDocumentAnalyzer)
    analyzer.scene_writing_max_output_tokens = 1000
    requests = []
    analyzer._collect_stream_text = lambda **kwargs: (
        requests.append(kwargs) or CollectedStream("INT. ROOM - DAY #1#\\n\\n@MAYA\\nI keep the key.", 30, ("STOP",))
    )
    plan = PlannedScene(
        scene_number=1,
        heading="INT. ROOM - DAY #1#",
        source_unit_ids=["unit1"],
        retained_event_ids=["event1"],
        omitted_event_ids=[],
        purpose="Maya decides what to do with the key.",
        characters=["MAYA"],
        causal_notes="The key matters.",
    )
    context = SceneContext(
        scene_number=1,
        source_unit_ids=("unit1",),
        retained_events=(),
        source_material=(
            ResolvedSourceMaterial(
                source_anchor=SourceAnchor.text(0, 20),
                content="Maya keeps the brass key.",
            ),
        ),
    )
    issues = [
        QualityIssue(
            scene_number=1,
            issue_type="event_omission",
            severity="major",
            event_ids=["event1"],
            preservation_condition="Keep Maya's decision.",
            description="The first draft drops the decision.",
            revision_direction="Restore the decision in Maya's line.",
        ),
        QualityIssue(
            scene_number=1,
            issue_type="motivation_change",
            severity="minor",
            event_ids=["event1"],
            preservation_condition="Preserve Maya's reason.",
            description="The line weakens her stated reason.",
            revision_direction="Keep the reason explicit.",
        ),
    ]

    revised = analyzer.revise_scene(
        plan,
        context,
        "INT. ROOM - DAY #1#\\n\\n@MAYA\\nI will leave it here.",
        issues,
        story_plan=None,
        analysis_context={"metadata": {"title": "Synthetic analysis"}},
        source_language="en",
    )
    prompt = requests[0]["contents"]

    assert revised.endswith("I keep the key.")
    assert "Maya keeps the brass key." in prompt
    assert "I will leave it here." in prompt
    assert "The first draft drops the decision." in prompt
    assert "Synthetic analysis" in prompt
    assert "Keep the reason explicit." in prompt
    assert "Parentheticals are optional" in prompt
    assert "Use only the exact names in planned_scene.characters for character cues" in prompt
    assert "Use planned_scene.heading verbatim" in prompt


def test_conversion_prompt_replaces_only_plot_irrelevant_minor_roles_with_narration() -> None:
    assert "no distinctive voice or identity" in CONVERSION_PROMPT
    assert "rewrite that" in CONVERSION_PROMPT.lower()
    assert "only case" in CONVERSION_PROMPT.lower()


def test_analysis_prompt_separates_stable_voice_traits_from_scene_direction() -> None:
    """Voice Design traits stay source-grounded; line acting belongs to Fountain."""
    parsed = FountainParser.parse("Title: Sample\\n\\nINT. ROOM - DAY #1#\\n\\nA line.\\n")
    prompt = VertexDocumentAnalyzer._analysis_prompt(parsed, max_main_characters=6)
    assert "Output exactly one or two short sentences" in prompt
    assert "scene-specific emotions" in prompt
    assert "Fountain parentheticals and TTS speech style" in prompt
    assert "natural human voice only" in prompt
    assert "figurative comparisons" in prompt
    assert "Do not assert a concrete age unless it is supported by the source character information" in prompt
    assert "Do not put scene-specific pace, pauses, emphasis, or line delivery in voice_traits" in prompt


def test_adaptation_result_exposes_stable_source_character_cards_only() -> None:
    from narravant.services.narrative_adaptation import (
        AdaptationResult,
        CharacterKnowledge,
        SourceCharacterCard,
    )

    result = AdaptationResult(
        fountain="",
        source_context=None,
        character_knowledge=(
            CharacterKnowledge(
                character_name="NOBU",
                evidence_event_ids=["event1"],
                defining_traits=["年齢が明記された老職人"],
                state_by_event={"event1": "この場面では怒っている"},
                knowledge_by_event={"event1": "秘密を知った"},
            ),
        ),
    )

    assert result.source_character_cards == (
        SourceCharacterCard("NOBU", ("年齢が明記された老職人",)),
    )


def test_fountain_parser_counts_utterances() -> None:
    """FountainParser must count utterances across scenes (narration and dialogue)."""
    script_text = (
        "Title: Sample\n\n"
        "INT. ROOM - DAY #1#\n\n"
        "The sun rose over the hills.\n\n"
        "@HERO\n"
        "We made it.\n\n"
        "EXT. FOREST - NIGHT #2#\n\n"
        "Darkness fell.\n"
    )
    parsed = FountainParser.parse(script_text)
    assert parsed.scene_count() == 2
    assert parsed.utterance_count() == 3


def test_conversion_normalization_wraps_bare_narration_with_narrator_cue() -> None:
    """Generated prose must not leave read-aloud narration as Fountain action."""
    fountain = "Title: 夕暮れ\n\nEXT. 港 - 夕方 #1#\n\n波が静かに岸を打つ。\n\n@メロス\n(不安げに)\n誰かいるのか。"

    canonical = normalize_single_conversion_scene_numbers(fountain)

    assert "@ナレーター\n(自然な語り口で)\n波が静かに岸を打つ。" in canonical
    parsed = FountainParser.parse(canonical)
    assert [(item.speaker, item.performance_direction, item.text) for item in parsed.all_utterances()] == [
        ("ナレーター", "自然な語り口で", "波が静かに岸を打つ。"),
        ("メロス", "不安げに", "誰かいるのか。"),
    ]


def test_conversion_normalization_preserves_fullwidth_narrator_direction() -> None:
    """Existing Japanese parentheticals count as guidance during normalization."""
    fountain = "Title: 夕暮れ\n\nEXT. 港 - 夕方 #1#\n\n（息をひそめて）\n\n波が静かに岸を打つ。"

    canonical = normalize_single_conversion_scene_numbers(fountain)

    assert "@ナレーター\n（息をひそめて）\n\n波が静かに岸を打つ。" in canonical
    assert "（自然な語り口で）" not in canonical


def test_sanity_check_rejects_script_with_zero_scenes() -> None:
    """A generated script with 0 scenes must fail sanity check safely."""
    empty_fountain = "Title: Only Title\n\nJust some raw outline text without scenes.\n"
    service, tasks = _create_service(StubEmptyAnalyzer(empty_fountain))
    content = b"Some source content"
    imported = validate_import(
        "test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)

    service.run(task_id, content)

    draft = service.drafts.get(task_id)
    assert draft is None
    events = tasks.events_after(task_id, 0)
    error_event = next((e for e in events if e.event_type == "error"), None)
    assert error_event is not None
    assert error_event.payload["code"] == "INVALID_SCRIPT_STRUCTURE"


def test_sanity_check_rejects_script_with_zero_utterances() -> None:
    """A generated script with scene headings but no action/dialogue must fail sanity check."""
    heading_only_fountain = "Title: Headings Only\n\nINT. ROOM - DAY #1#\n\nEXT. PARK - NIGHT #2#\n"
    service, tasks = _create_service(StubEmptyAnalyzer(heading_only_fountain))
    content = b"Some source content"
    imported = validate_import(
        "test.fountain",
        content,
        1024 * 1024,
        pdf_max_pages=10,
        fdx_max_depth=10,
        txt_minimum_confidence=0.7,
    )
    task_id = service.start("owner-1", imported)

    service.run(task_id, content)

    draft = service.drafts.get(task_id)
    assert draft is None
    events = tasks.events_after(task_id, 0)
    error_event = next((e for e in events if e.event_type == "error"), None)
    assert error_event is not None
    assert error_event.payload["code"] == "INVALID_SCRIPT_STRUCTURE"
