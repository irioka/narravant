"""Tests for Task 10 (C2t): Adaptation prompt relaxation and structural sanity check."""

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
    """Directions must be inferred for performance, not copied from stage directions."""
    assert "immediate situation" in CONVERSION_PROMPT
    assert "dialogue's wording" in CONVERSION_PROMPT
    assert "Do not merely copy" in CONVERSION_PROMPT


def test_conversion_prompt_replaces_only_plot_irrelevant_minor_roles_with_narration() -> None:
    assert "no distinctive voice or identity" in CONVERSION_PROMPT
    assert "rewrite that" in CONVERSION_PROMPT.lower()
    assert "only case" in CONVERSION_PROMPT.lower()


def test_analysis_prompt_separates_stable_voice_traits_from_scene_direction() -> None:
    """Voice Design traits stay provider-friendly; line acting belongs to parentheticals."""
    parsed = FountainParser.parse("Title: Sample\n\nINT. ROOM - DAY #1#\n\nA line.\n")
    prompt = VertexDocumentAnalyzer._analysis_prompt(parsed, max_main_characters=6)
    assert "Output exactly one or two short sentences" in prompt
    assert "scene-specific emotions" in prompt
    assert "Fountain parentheticals and TTS speech style" in prompt
    assert "natural human voice only" in prompt
    assert "figurative comparisons" in prompt


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
