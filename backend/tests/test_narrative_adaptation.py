"""Synthetic tests for in-memory narrative adaptation, PDF windowing, and causal DAG."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from io import BytesIO
from unittest.mock import patch

import pytest
from pypdf import PageObject, PdfReader, PdfWriter

from narravant.core.content_language import ContentLanguageMismatch
from narravant.core.generated_fountain import GeneratedFountainCueError
from narravant.services.narrative_adaptation import (
    AdaptationResult,
    CausalEdge,
    CausalPlotGraph,
    CharacterKnowledge,
    MergedPdfReaderData,
    NarrativeAdaptationConfig,
    NarrativeAdaptationError,
    NarrativeAdaptationService,
    NarrativePlan,
    PdfPageWindow,
    PdfWindowClassification,
    PdfWindowKind,
    PlannedScene,
    QualityIssue,
    ReaderCandidateEvent,
    ReaderWindowResult,
    SceneContext,
    SourceAnchor,
    SourceContext,
    SourceKind,
    SourceTurningPoint,
    SourceUnit,
    StoryEvent,
    StoryPlan,
    aggregate_pdf_classifications,
    build_causal_dag,
    merge_pdf_reader_candidates,
    split_pdf_windows,
)


def _create_synthetic_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_page(PageObject.create_blank_page(width=100, height=100))
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_split_pdf_windows_creates_correct_windows_without_extracting_text() -> None:
    pdf_bytes = _create_synthetic_pdf(3)

    with patch.object(
        PageObject,
        "extract_text",
        side_effect=AssertionError("extract_text must not be called"),
    ):
        windows_no_overlap = split_pdf_windows(pdf_bytes, page_window=2, overlap=0)
        windows_with_overlap = split_pdf_windows(pdf_bytes, page_window=2, overlap=1)

    assert [(w.start_page, w.end_page) for w in windows_no_overlap] == [(1, 2), (3, 3)]
    assert all(w.pdf_bytes.startswith(b"%PDF-") for w in windows_no_overlap)

    assert [(w.start_page, w.end_page) for w in windows_with_overlap] == [
        (1, 2),
        (2, 3),
    ]
    assert all(w.pdf_bytes.startswith(b"%PDF-") for w in windows_with_overlap)


def test_aggregate_pdf_classifications_applies_screenplay_first_and_handles_inconclusive() -> None:
    w1 = PdfPageWindow(start_page=1, end_page=2, pdf_bytes=b"%PDF-stub1")
    w2 = PdfPageWindow(start_page=3, end_page=4, pdf_bytes=b"%PDF-stub2")
    w3 = PdfPageWindow(start_page=5, end_page=6, pdf_bytes=b"%PDF-stub3")

    cover = PdfWindowClassification(window=w1, kind=PdfWindowKind.NO_STORY_CONTENT)
    narrative = PdfWindowClassification(window=w2, kind=PdfWindowKind.NARRATIVE_PROSE)
    screenplay = PdfWindowClassification(window=w3, kind=PdfWindowKind.SCREENPLAY_EVIDENCE)
    inconclusive = PdfWindowClassification(window=w2, kind=PdfWindowKind.INCONCLUSIVE)

    # 1. Screenplay-First: even a single screenplay evidence makes the whole doc SCREENPLAY
    assert aggregate_pdf_classifications([cover, narrative, screenplay]) == SourceKind.SCREENPLAY

    # 2. Pure prose: cover + narrative -> NARRATIVE_PROSE
    assert aggregate_pdf_classifications([cover, narrative]) == SourceKind.NARRATIVE_PROSE

    # 3. Inconclusive window causes failure
    with pytest.raises(NarrativeAdaptationError, match="SOURCE_CLASSIFICATION_INCONCLUSIVE"):
        aggregate_pdf_classifications([narrative, inconclusive])

    # 4. No story content alone causes failure
    with pytest.raises(NarrativeAdaptationError, match="SOURCE_CLASSIFICATION_INCONCLUSIVE"):
        aggregate_pdf_classifications([cover, cover])


def test_merge_pdf_reader_candidates_overlap_deduplication_and_window_ownership() -> None:
    w1 = PdfPageWindow(start_page=1, end_page=6, pdf_bytes=b"%PDF-1-6")
    w2 = PdfPageWindow(start_page=6, end_page=11, pdf_bytes=b"%PDF-6-11")

    e_page6_w1 = ReaderCandidateEvent(
        summary="主人公が 鍵を発見する。",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=6),
    )
    # Same event from the overlapping window, with whitespace differences.
    e_page6_w2_duplicate = ReaderCandidateEvent(
        summary="主人公が  鍵を発見する。 ",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=6),
    )
    # The same concise summary can describe a different event when its evidence differs.
    e_page6_w2_distinct = ReaderCandidateEvent(
        summary="主人公が 鍵を発見する。",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=6),
        character_states={"主人公": "鍵を隠す"},
    )
    e_page6_7_w2 = ReaderCandidateEvent(
        summary="部屋を出る",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=7),
    )

    r1 = ReaderWindowResult(window=w1, candidate_events=[e_page6_w1])
    r2 = ReaderWindowResult(
        window=w2,
        candidate_events=[e_page6_w2_duplicate, e_page6_w2_distinct, e_page6_7_w2],
    )

    merged: MergedPdfReaderData = merge_pdf_reader_candidates([r1, r2])

    assert len(merged.events) == 3
    p6_events = [
        event
        for event in merged.events
        if event.source_anchor.start_page == 6 and event.source_anchor.end_page == 6
    ]
    assert len(p6_events) == 2
    assert {event.character_states.get("主人公") for event in p6_events} == {None, "鍵を隠す"}

    p6_7_events = [
        event
        for event in merged.events
        if event.source_anchor.start_page == 6 and event.source_anchor.end_page == 7
    ]
    assert len(p6_7_events) == 1
    assert all(event.event_id.startswith("ev_") for event in merged.events)


def test_build_causal_dag_cycle_breaking_and_linearization() -> None:
    # 4 events: e1 (start 0), e2 (start 100), e3 (start 200), e4 (start 300)
    e1 = StoryEvent(event_id="e1", summary="Event 1", source_anchor=SourceAnchor.text(0, 50))
    e2 = StoryEvent(event_id="e2", summary="Event 2", source_anchor=SourceAnchor.text(100, 150))
    e3 = StoryEvent(event_id="e3", summary="Event 3", source_anchor=SourceAnchor.text(200, 250))
    e4 = StoryEvent(event_id="e4", summary="Event 4", source_anchor=SourceAnchor.text(300, 350))
    events = [e1, e2, e3, e4]

    # Edges:
    # e1 -> e2 (high)
    # e2 -> e3 (high)
    # e3 -> e1 (low: forms cycle e1 -> e2 -> e3 -> e1, should be dropped)
    # e2 -> e4 (medium)
    edge_1_2 = CausalEdge(from_node_id="e1", to_node_id="e2", strength="high")
    edge_2_3 = CausalEdge(from_node_id="e2", to_node_id="e3", strength="high")
    edge_3_1 = CausalEdge(from_node_id="e3", to_node_id="e1", strength="low")
    edge_2_4 = CausalEdge(from_node_id="e2", to_node_id="e4", strength="medium")
    candidate_edges = [edge_3_1, edge_1_2, edge_2_3, edge_2_4]

    dag: CausalPlotGraph = build_causal_dag(events, candidate_edges)

    # edge_3_1 must be removed to break the cycle
    assert edge_3_1 not in dag.edges
    assert edge_1_2 in dag.edges
    assert edge_2_3 in dag.edges
    assert edge_2_4 in dag.edges

    # Breadth-first / Kahn topological order:
    # Layer 0: e1
    # Layer 1: e2
    # Layer 2: e3, e4 (ordered by source anchor start)
    ordered_ids = dag.breadth_first_event_ids()
    assert ordered_ids == ["e1", "e2", "e3", "e4"]


class FakeNarrativeClient:
    def __init__(self) -> None:
        self.verify_results: dict[int, list[tuple[bool, str | None]]] = {}
        self.issue_batches: dict[int, list[list[str]]] = {}
        self.invalid_verification_reference = False
        self.revision_requests: list[tuple[int, str, tuple[QualityIssue, ...], SceneContext, StoryPlan]] = []
        self.write_counts: dict[int, int] = defaultdict(int)
        self.verified_scenes: list[int] = []
        self.verified_analysis_contexts: list[dict[str, object]] = []
        self.read_languages: list[str] = []
        self.plan_languages: list[str] = []
        self.write_languages: list[str] = []
        self.written_contexts: dict[int, object] = {}
        self.invalid_source_unit_reference = False

    def classify_text_source(self, text: str, on_progress=None, deadline=None) -> SourceKind:
        if on_progress:
            on_progress(42)
        return SourceKind.NARRATIVE_PROSE

    def classify_pdf_window(self, window: PdfPageWindow, on_progress=None, deadline=None) -> PdfWindowClassification:
        if on_progress:
            on_progress(42)
        return PdfWindowClassification(window=window, kind=PdfWindowKind.NARRATIVE_PROSE)

    def read_text_window(
        self,
        text_window: str,
        source_anchor: SourceAnchor,
        on_progress=None,
        deadline=None,
        *,
        source_language="und",
    ) -> list[ReaderCandidateEvent]:
        self.read_languages.append(source_language)
        start = source_anchor.start
        end = source_anchor.end
        middle = start + (end - start) // 2
        first_anchor = SourceAnchor.text(start, middle) if middle > start else SourceAnchor.text(start, end)
        second_anchor = SourceAnchor.text(middle, end) if middle < end else first_anchor
        return [
            ReaderCandidateEvent(
                summary="アリスが庭を歩く",
                source_anchor=first_anchor,
                character_states={"アリス": "庭を歩き、周囲に興味を示す"},
                knowledge_changes={"アリス": "庭にいる"},
            ),
            ReaderCandidateEvent(
                summary="ウサギが走る",
                source_anchor=second_anchor,
                character_states={"ウサギ": "急いで走る"},
                relationship_changes={"アリス": "ウサギに関心を持ち始める"},
            ),
        ]

    def read_pdf_window(self, window: PdfPageWindow, on_progress=None, deadline=None) -> ReaderWindowResult:
        page_anchor = SourceAnchor.pdf(window.start_page, window.start_page)
        return ReaderWindowResult(
            window=window,
            candidate_events=[
                ReaderCandidateEvent(
                    summary="アリスが庭を歩く",
                    source_anchor=page_anchor,
                    character_states={"アリス": "庭を歩き、周囲に興味を示す"},
                ),
                ReaderCandidateEvent(
                    summary="ウサギが走る",
                    source_anchor=page_anchor,
                    character_states={"ウサギ": "急いで走る"},
                ),
            ],
            candidate_source_units=[
                SourceUnit(
                    unit_id=f"su_p{window.start_page}_{window.end_page}",
                    source_anchor=SourceAnchor.pdf(window.start_page, window.end_page),
                )
            ],
        )

    def build_causal_edges(self, events: Sequence[StoryEvent], on_progress=None, deadline=None) -> list[CausalEdge]:
        if len(events) >= 2:
            return [
                CausalEdge(
                    from_node_id=events[0].event_id,
                    to_node_id=events[1].event_id,
                    strength="high",
                )
            ]
        return []

    def plan_scenes(
        self,
        dag: CausalPlotGraph,
        source_context: SourceContext,
        character_knowledge: Sequence[CharacterKnowledge],
        on_progress=None,
        deadline=None,
        *,
        source_language="und",
    ) -> NarrativePlan:
        self.plan_languages.append(source_language)
        first_event = dag.events[0]
        turning_points = [
            SourceTurningPoint(
                tp_number=1,
                label="Opportunity",
                availability="identified",
                event_ids=[first_event.event_id],
                evidence=[first_event.source_anchor],
                change="アリスが物語の出来事へ関わる機会を得る。",
            )
        ] + [
            SourceTurningPoint(
                tp_number=tp_number,
                label=label,
                availability="not_applicable",
                reason="この短い合成原作には独立した該当イベントがありません。",
            )
            for tp_number, label in [
                (2, "Change of Plans"),
                (3, "Point of No Return"),
                (4, "Major Setback"),
                (5, "Climax"),
            ]
        ]
        story_plan = StoryPlan(
            turning_points=turning_points,
            important_event_ids=[event.event_id for event in dag.events],
            character_motivations={"アリス": "周囲を知りたい", "ウサギ": "急いでいる"},
            central_theme="好奇心から始まる冒険",
            intended_outcome="アリスが未知の世界へ進む。",
            presentation_order_event_ids=[event.event_id for event in dag.events],
        )
        return NarrativePlan(
            story_plan=story_plan,
            character_knowledge=list(character_knowledge),
            planned_scenes=[
                PlannedScene(
                    scene_number=1,
                    heading="EXT. GARDEN - DAY #1#",
                    source_unit_ids=["su_missing" if self.invalid_source_unit_reference else "su_0001"],
                    retained_event_ids=[dag.events[0].event_id],
                    purpose="日常の提示",
                    characters=["アリス"],
                    causal_notes="始まり",
                ),
                PlannedScene(
                    scene_number=2,
                    heading="EXT. PATH - DAY #2#",
                    source_unit_ids=["su_missing" if self.invalid_source_unit_reference else "su_0001"],
                    retained_event_ids=[dag.events[1].event_id],
                    purpose="事件発生",
                    characters=["アリス", "ウサギ"],
                    causal_notes="追いかける",
                    dependency_scene_numbers=[1],
                ),
                PlannedScene(
                    scene_number=3,
                    heading="INT. RABBIT HOLE - DAY #3#",
                    source_unit_ids=["su_missing" if self.invalid_source_unit_reference else "su_0001"],
                    retained_event_ids=[dag.events[1].event_id],
                    purpose="落下",
                    characters=["アリス"],
                    causal_notes="穴へ落ちる",
                    dependency_scene_numbers=[2],
                ),
            ],
        )

    def write_scene(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
        on_progress=None,
        deadline=None,
        *,
        source_language="und",
    ) -> str:
        self.write_languages.append(source_language)
        self.written_contexts[scene_plan.scene_number] = scene_context
        self.write_counts[scene_plan.scene_number] += 1
        if source_language == "ja":
            return (
                f"{scene_plan.heading}\n\n"
                f"シーン{scene_plan.scene_number}の描写です（v{self.write_counts[scene_plan.scene_number]}）。\n"
            )
        return (
            f"{scene_plan.heading}\n\n"
            f"Action for scene {scene_plan.scene_number} (v{self.write_counts[scene_plan.scene_number]}).\n"
        )

    def _make_quality_issue(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
        description: str,
    ) -> QualityIssue:
        event_ids = (
            ["unknown-event"] if self.invalid_verification_reference else scene_plan.retained_event_ids[:1]
        )
        source_anchors = [
            scene_context.source_material[0].source_anchor
        ] if scene_context.source_material else []
        return QualityIssue(
            scene_number=scene_plan.scene_number,
            issue_type="event_omission",
            severity="major",
            event_ids=event_ids,
            source_anchors=source_anchors,
            preservation_condition="Keep the planned event and its source meaning.",
            description=description,
            revision_direction="Restore the missing event locally.",
        )

    def verify_scene(
        self,
        scene_plan: PlannedScene,
        scene_text: str,
        *,
        scene_context: SceneContext,
        story_plan: StoryPlan,
        analysis_context: dict[str, object] | None = None,
        on_progress=None,
        deadline=None,
    ) -> list[QualityIssue]:
        self.verified_scenes.append(scene_plan.scene_number)
        self.verified_analysis_contexts.append(analysis_context or {})
        batches = self.issue_batches.get(scene_plan.scene_number)
        if batches:
            descriptions = batches.pop(0)
            return [
                self._make_quality_issue(scene_plan, scene_context, description)
                for description in descriptions
            ]
        results = self.verify_results.get(scene_plan.scene_number)
        if results:
            passed, reason = results.pop(0)
            if not passed:
                return [self._make_quality_issue(scene_plan, scene_context, reason or "Synthetic issue")]
        return []

    def revise_scene(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
        scene_text: str,
        issues: Sequence[QualityIssue],
        story_plan: StoryPlan,
        *,
        analysis_context: dict[str, object] | None = None,
        on_progress=None,
        deadline=None,
        source_language="und",
    ) -> str:
        self.verified_analysis_contexts.append(analysis_context or {})
        self.revision_requests.append(
            (scene_plan.scene_number, scene_text, tuple(issues), scene_context, story_plan)
        )
        return self.write_scene(
            scene_plan,
            scene_context,
            deadline=deadline,
            source_language=source_language,
        )

    def normalize_screenplay_text(
        self, text: str, on_progress=None, deadline=None, *, source_language="und"
    ) -> str:
        return text

    def normalize_screenplay_pdf(self, source_pdf: bytes, on_progress=None, deadline=None) -> str:
        return "Title: PDF Screenplay\n\nINT. ROOM - DAY #1#\n\nAction.\n"


def _make_test_config() -> NarrativeAdaptationConfig:
    return NarrativeAdaptationConfig(
        text_reader_source_unit_max_characters=1000,
        text_reader_source_unit_overlap_characters=100,
        pdf_classifier_page_window=8,
        pdf_reader_page_window=6,
        pdf_reader_page_overlap=1,
        reader_refinement_max_attempts=2,
        scene_regeneration_max_attempts=2,
        classification_max_output_tokens=2048,
        reader_max_output_tokens=8192,
        planning_max_output_tokens=16384,
        scene_writing_max_output_tokens=16384,
        verification_max_output_tokens=4096,
    )


def _verify_adaptation(service: NarrativeAdaptationService, result: AdaptationResult) -> AdaptationResult:
    return service.verify_after_analysis(
        result,
        {"metadata": {"title": "Synthetic analysis"}},
        ensure_not_cancelled=lambda: None,
    )


def test_text_reader_skips_explicit_non_story_window_but_preserves_story() -> None:
    client = FakeNarrativeClient()
    original_reader = client.read_text_window
    config = _make_test_config().model_copy(
        update={
            "text_reader_source_unit_max_characters": 30,
            "text_reader_source_unit_overlap_characters": 0,
        }
    )
    source = "アリスが庭を歩き、ウサギを追いかける。".ljust(30) + "発行日・出版社・底本情報"
    calls = []

    def read(text, anchor, **kwargs):
        calls.append(anchor.start)
        return original_reader(text, anchor, **kwargs) if anchor.start == 0 else None

    client.read_text_window = read
    result = NarrativeAdaptationService(client, config).adapt_text(source)
    assert result.fountain
    assert calls == [0, 30]


@pytest.mark.parametrize("response", [None, []])
def test_text_reader_rejects_document_without_any_events(response) -> None:
    client = FakeNarrativeClient()
    client.read_text_window = lambda *args, **kwargs: response
    with pytest.raises(NarrativeAdaptationError) as exc:
        NarrativeAdaptationService(client, _make_test_config()).adapt_text("合成データ")
    assert exc.value.code == "ADAPTATION_VERIFICATION_FAILED"


def test_text_reader_rejects_invalid_anchor_after_bounded_retries(caplog) -> None:
    client = FakeNarrativeClient()
    calls = []

    def read(text, anchor, **kwargs):
        calls.append(anchor.start)
        return [ReaderCandidateEvent(summary="出来事", source_anchor=SourceAnchor.text(0, 999))]

    client.read_text_window = read
    with pytest.raises(NarrativeAdaptationError) as exc:
        NarrativeAdaptationService(client, _make_test_config()).adapt_text("合成原稿")
    assert exc.value.code == "ADAPTATION_VERIFICATION_FAILED"
    assert calls == [0, 0]
    assert "invalid_event_anchor_or_summary" in caplog.text
    assert "合成原稿" not in caplog.text


@pytest.mark.parametrize("max_attempts", [1, 2, 3])
def test_reader_max_attempts_includes_initial_read(max_attempts) -> None:
    client = FakeNarrativeClient()
    calls = []

    def read(text, anchor, **kwargs):
        calls.append(anchor.start)
        return [ReaderCandidateEvent(summary="出来事", source_anchor=SourceAnchor.text(0, 999))]

    client.read_text_window = read
    config = _make_test_config().model_copy(update={"reader_refinement_max_attempts": max_attempts})

    with pytest.raises(NarrativeAdaptationError):
        NarrativeAdaptationService(client, config).adapt_text("合成原稿")

    assert len(calls) == max_attempts


def test_text_reader_unexplained_empty_response_retries_and_keeps_story() -> None:
    client = FakeNarrativeClient()
    original_reader = client.read_text_window
    calls = []

    def read(text, anchor, **kwargs):
        calls.append(anchor.start)
        return [] if len(calls) == 1 else original_reader(text, anchor, **kwargs)

    client.read_text_window = read
    result = NarrativeAdaptationService(client, _make_test_config()).adapt_text("アリスはウサギを追う。")
    assert result.fountain
    assert calls == [0, 0]


def test_narrative_adaptation_service_adapts_text_prose_successfully() -> None:
    client = FakeNarrativeClient()
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    phases_reported: list[str] = []

    def on_progress(phase: str, _count: int) -> None:
        phases_reported.append(phase)

    result = service.adapt_text(
        "原作小説の長いテキストです。",
        on_progress=on_progress,
        ensure_not_cancelled=lambda: None,
    )

    assert "EXT. GARDEN - DAY #1#" in result.fountain
    assert "EXT. PATH - DAY #2#" in result.fountain
    assert "INT. RABBIT HOLE - DAY #3#" in result.fountain
    assert result.story_plan is not None
    assert len(result.story_plan.turning_points) == 5
    assert sum(point.availability == "not_applicable" for point in result.story_plan.turning_points) == 4
    assert len(result.planned_scenes) == 3
    alice = next(item for item in result.character_knowledge if item.character_name == "アリス")
    assert alice.knowledge_by_event
    assert alice.relationship_by_event
    assert "reading" in phases_reported
    assert "building_plot_graph" in phases_reported
    assert "planning" in phases_reported
    assert "writing" in phases_reported
    service.verify_after_analysis(
        result,
        {"metadata": {"title": "Synthetic analysis"}},
        on_progress=on_progress,
        ensure_not_cancelled=lambda: None,
    )
    assert "verifying" in phases_reported


def test_adapt_text_returns_resolved_source_context_in_internal_result() -> None:
    source_text = "原作小説の長いテキストです。"
    result = NarrativeAdaptationService(FakeNarrativeClient(), _make_test_config()).adapt_text(
        source_text,
        ensure_not_cancelled=lambda: None,
    )

    assert result.fountain.startswith("EXT. GARDEN - DAY #1#")
    assert result.source_context.source_text == source_text
    assert result.scene_contexts[1].source_material[0].content == source_text


def test_scene_writer_receives_resolved_original_source_material() -> None:
    source_text = "原文本文: ミナは青い鍵を古い歌集の後ろに隠した。"
    client = FakeNarrativeClient()

    NarrativeAdaptationService(client, _make_test_config()).adapt_text(
        source_text,
        ensure_not_cancelled=lambda: None,
    )

    context = client.written_contexts[1]
    assert isinstance(context, SceneContext)
    assert context.source_material[0].content == source_text


def test_scene_writer_receives_original_and_transitive_long_distance_context() -> None:
    source_text = "原作本文では青い鍵に三本の刻みがある。"
    client = FakeNarrativeClient()
    plan_scenes = client.plan_scenes
    write_scene = client.write_scene

    def plan_with_five_scenes(*args, **kwargs):
        narrative_plan = plan_scenes(*args, **kwargs)
        event_id = args[0].events[1].event_id
        narrative_plan.planned_scenes.extend(
            [
                PlannedScene(
                    scene_number=4,
                    heading="INT. ARCHIVE - DAY #4#",
                    source_unit_ids=["su_0001"],
                    retained_event_ids=[event_id],
                    purpose="手掛かりを保つ。",
                    characters=["アリス"],
                    causal_notes="前の発見を保持する。",
                    dependency_scene_numbers=[3],
                ),
                PlannedScene(
                    scene_number=5,
                    heading="INT. TOWER - NIGHT #5#",
                    source_unit_ids=["su_0001"],
                    retained_event_ids=[event_id],
                    purpose="古い手掛かりを回収する。",
                    characters=["アリス", "ウサギ"],
                    causal_notes="遠い場面の情報と一致させる。",
                    dependency_scene_numbers=[4],
                ),
            ]
        )
        return narrative_plan

    def write_with_early_clue(plan, context, **kwargs):
        result = write_scene(plan, context, **kwargs)
        if plan.scene_number == 1:
            return f"{plan.heading}\n\n青い鍵の刻みは三本だった。\n"
        return result

    client.plan_scenes = plan_with_five_scenes
    client.write_scene = write_with_early_clue

    NarrativeAdaptationService(client, _make_test_config()).adapt_text(
        source_text,
        ensure_not_cancelled=lambda: None,
    )

    context = client.written_contexts[5]
    assert isinstance(context, SceneContext)
    assert context.source_material[0].content == source_text
    related = dict(context.related_previous_scenes)
    assert "青い鍵の刻みは三本だった。" in related[1]
    assert 4 in related
    alice = next(item for item in client.written_contexts[1].character_knowledge if item.character_name == "アリス")
    assert alice.evidence_event_ids == ["ev_0001"]


def test_narrative_adaptation_rejects_unknown_scene_source_unit() -> None:
    client = FakeNarrativeClient()
    client.invalid_source_unit_reference = True

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        NarrativeAdaptationService(client, _make_test_config()).adapt_text(
            "原作小説の長いテキストです。",
            ensure_not_cancelled=lambda: None,
        )


def test_source_context_resolves_original_pdf_pages_for_a_scene() -> None:
    source_pdf = _create_synthetic_pdf(3)
    result = NarrativeAdaptationService(FakeNarrativeClient(), _make_test_config()).adapt_pdf(
        source_pdf,
        ensure_not_cancelled=lambda: None,
    )

    material = result.scene_contexts[1].source_material[0]
    assert material.source_anchor == SourceAnchor.pdf(1, 3)
    assert isinstance(material.content, bytes)
    assert len(PdfReader(BytesIO(material.content)).pages) == 3


def test_source_context_rejects_out_of_range_source_unit_anchors() -> None:
    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        SourceContext.from_text(
            "short",
            [SourceUnit(unit_id="su_0001", source_anchor=SourceAnchor.text(0, 6))],
        )

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        SourceContext.from_pdf(
            _create_synthetic_pdf(3),
            [SourceUnit(unit_id="su_0001", source_anchor=SourceAnchor.pdf(1, 4))],
        )


def test_source_context_rejects_event_outside_scene_source_units() -> None:
    source_context = SourceContext.from_text(
        "0123456789abcdefghij",
        [
            SourceUnit(unit_id="su_0001", source_anchor=SourceAnchor.text(0, 10)),
            SourceUnit(unit_id="su_0002", source_anchor=SourceAnchor.text(10, 20)),
        ],
    )
    event = StoryEvent(
        event_id="ev_0001",
        summary="合成イベント",
        source_anchor=SourceAnchor.text(11, 15),
    )
    plan = PlannedScene(
        scene_number=1,
        heading="INT. ROOM - DAY #1#",
        source_unit_ids=["su_0001"],
        retained_event_ids=["ev_0001"],
        purpose="合成目的",
        characters=["話者"],
        causal_notes="合成因果",
    )

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        source_context.resolve_scene(plan, [event])


def test_narrative_adaptation_rejects_unaccounted_source_events() -> None:
    client = FakeNarrativeClient()
    original_plan = client.plan_scenes

    def drop_last_scene(*args, **kwargs) -> NarrativePlan:
        plan = original_plan(*args, **kwargs)
        return plan.model_copy(update={"planned_scenes": plan.planned_scenes[:1]})

    service = NarrativeAdaptationService(client, _make_test_config())
    with patch.object(client, "plan_scenes", side_effect=drop_last_scene):
        with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
            service.adapt_text("原作小説の長いテキストです。")


def test_narrative_adaptation_rejects_forward_scene_dependencies() -> None:
    client = FakeNarrativeClient()
    original_plan = client.plan_scenes

    def add_forward_dependency(*args, **kwargs) -> NarrativePlan:
        plan = original_plan(*args, **kwargs)
        scenes = list(plan.planned_scenes)
        scenes[0] = scenes[0].model_copy(update={"dependency_scene_numbers": [2]})
        return plan.model_copy(update={"planned_scenes": scenes})

    service = NarrativeAdaptationService(client, _make_test_config())
    with patch.object(client, "plan_scenes", side_effect=add_forward_dependency):
        with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
            service.adapt_text("原作小説の長いテキストです。")


def test_narrative_adaptation_service_retries_rejected_scene_and_reverifies_downstream() -> None:
    client = FakeNarrativeClient()
    # Scene 2 fails first time, passes on retry.
    # Scene 3 depends on scene 2, so after scene 2 is regenerated and passes,
    # scene 3 must be reverified!
    client.verify_results[2] = [(False, "Fountain syntax error"), (True, None)]
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    result = service.adapt_text("原作本文", on_progress=lambda *_: None, ensure_not_cancelled=lambda: None)
    _verify_adaptation(service, result)

    # Scene 2 was written twice
    assert client.write_counts[2] == 2
    # Scene 2 was verified twice
    assert client.verified_scenes.count(2) == 2
    # Scene 3 was verified at least twice (initial pass + downstream re-verification)
    assert client.verified_scenes.count(3) == 2


def test_multiple_quality_issues_share_one_local_revision() -> None:
    client = FakeNarrativeClient()
    client.issue_batches[1] = [["one omitted event", "one motivation mismatch"], []]

    service = NarrativeAdaptationService(client, _make_test_config())
    result = _verify_adaptation(service, service.adapt_text("合成原作"))

    assert len(client.revision_requests) == 1
    scene_number, initial_text, issues, scene_context, story_plan = client.revision_requests[0]
    assert scene_number == 1
    assert "Action for scene 1" in initial_text
    assert len(issues) == 2
    assert scene_context.source_material
    assert story_plan.intended_outcome
    assert client.verified_analysis_contexts[0]["metadata"] == {"title": "Synthetic analysis"}
    assert result.revision_count[1] == 1


@pytest.mark.parametrize("downstream_reverification", [False, True])
def test_minor_issue_after_cue_repair_preserves_scene_without_exceeding_budget(
    downstream_reverification, caplog,
) -> None:
    client = FakeNarrativeClient()
    write = client.write_scene
    verify = client.verify_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 2 and client.write_counts[2] == 1:
            return f"{plan.heading}\n\n@アリス\n<breath>\n"
        return valid

    def verify_with_minor_issue(plan, text, **kwargs):
        issues = verify(plan, text, **kwargs)
        if plan.scene_number == 2:
            return [issue.model_copy(update={"severity": "minor"}) for issue in issues]
        return issues

    client.write_scene = generate
    client.verify_scene = verify_with_minor_issue
    if downstream_reverification:
        client.verify_results[1] = [(False, "PRIVATE_UPSTREAM_ISSUE"), (True, None)]
        client.verify_results[2] = [(True, None), (False, "PRIVATE_MINOR_ISSUE")]
    else:
        client.verify_results[2] = [(False, "PRIVATE_MINOR_ISSUE")]
    service = NarrativeAdaptationService(client, _make_test_config())
    adaptation = service.adapt_text("合成小説")
    caplog.set_level("INFO", logger="narravant.services.narrative_adaptation")

    result = _verify_adaptation(service, adaptation)

    assert result.scene_fountains[2] == adaptation.scene_fountains[2]
    assert result.revision_count[2] == 1
    assert client.write_counts[2] == 2
    assert sum(request[0] == 2 for request in client.revision_requests) == 1
    assert client.revision_requests[0][2][0].issue_type == "fountain_invalid"
    assert "reason=missing_speech" in caplog.text
    assert client.verified_scenes.count(2) == (2 if downstream_reverification else 1)
    assert "reason=minor_issues_without_revision_budget" in caplog.text
    assert "scene_number=2" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize("resolved", [False, True])
def test_two_retries_share_structure_and_content_repairs_before_success_or_failure(resolved) -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 2 and client.write_counts[2] == 1:
            return f"{plan.heading}\n\n@アリス\n<breath>\n"
        return valid

    client.write_scene = generate
    client.verify_results[2] = [
        (False, "PRIVATE_FIRST_ISSUE"),
        (resolved, None if resolved else "PRIVATE_REMAINING_ISSUE"),
    ]
    config = _make_test_config().model_copy(update={"scene_regeneration_max_attempts": 3})
    service = NarrativeAdaptationService(client, config)
    adaptation = service.adapt_text("合成小説")

    if resolved:
        result = _verify_adaptation(service, adaptation)
        assert result.revision_count[2] == 2
        assert result.scene_fountains[2].endswith("(v3).\n")
    else:
        with pytest.raises(NarrativeAdaptationError) as exc:
            _verify_adaptation(service, adaptation)
        assert exc.value.context == {
            "reason": "major_issues_after_revision",
            "scene_number": 2,
            "revision_count": 2,
            "max_retries": 2,
            "issue_types": ["event_omission"],
        }
        assert "PRIVATE_" not in str(exc.value.context)
    assert client.write_counts[2] == 3
    assert len(client.revision_requests) == 2


@pytest.mark.parametrize("max_attempts", [1, 2, 3])
def test_scene_max_attempts_includes_initial_write_and_reports_retry_budget(max_attempts) -> None:
    client = FakeNarrativeClient()
    client.verify_results[2] = [(False, "PRIVATE_QUALITY_ISSUE")] * (max_attempts + 1)
    config = _make_test_config().model_copy(update={"scene_regeneration_max_attempts": max_attempts})
    service = NarrativeAdaptationService(client, config)
    adaptation = service.adapt_text("合成小説")

    with pytest.raises(NarrativeAdaptationError) as exc:
        _verify_adaptation(service, adaptation)

    assert client.write_counts[2] == max_attempts
    assert exc.value.context["revision_count"] == max_attempts - 1
    assert exc.value.context["max_retries"] == max_attempts - 1


def test_invalid_heading_in_first_content_revision_uses_second_retry() -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 2 and client.write_counts[2] == 2:
            return "INT. WRONG ROOM - NIGHT #99#\n\nA synthetic scene.\n"
        return valid

    client.write_scene = generate
    client.verify_results[2] = [(False, "PRIVATE_SOURCE_ISSUE"), (True, None)]
    config = _make_test_config().model_copy(update={"scene_regeneration_max_attempts": 3})
    service = NarrativeAdaptationService(client, config)
    result = _verify_adaptation(service, service.adapt_text("合成小説"))

    assert result.revision_count[2] == 2
    assert client.write_counts[2] == 3
    assert "WRONG ROOM" not in result.fountain
    assert result.planned_scenes[1].heading in result.fountain


@pytest.mark.parametrize(
    ("failure_stage", "expected_reason"),
    [
        ("budget", "revision_budget_exhausted"),
        ("after_revision", "major_issues_after_revision"),
        ("final", "major_issues_in_final_verification"),
    ],
)
def test_major_issue_rejection_logs_safe_reason_and_scene(failure_stage, expected_reason, caplog) -> None:
    client = FakeNarrativeClient()
    client.verify_results[2] = [(False, "PRIVATE_QUALITY_DESCRIPTION"), (False, "PRIVATE_QUALITY_DESCRIPTION")]
    config = _make_test_config()
    if failure_stage == "budget":
        config = config.model_copy(update={"scene_regeneration_max_attempts": 1})
    service = NarrativeAdaptationService(client, config)
    adaptation = service.adapt_text("合成小説")

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        service.verify_after_analysis(
            adaptation, {"metadata": {"title": "PRIVATE_ANALYSIS"}}, allow_revision=failure_stage != "final",
        )

    assert f"reason={expected_reason}" in caplog.text
    assert "scene_number=2" in caplog.text
    assert "major_issue_count=1" in caplog.text
    assert "revision_count=" in caplog.text
    assert f"max_attempts={config.scene_regeneration_max_attempts}" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_after_analysis_revision_must_preserve_planned_scene_heading_and_number() -> None:
    client = FakeNarrativeClient()
    client.issue_batches[1] = [["synthetic omission"]]
    client.revise_scene = lambda plan, *_args, **_kwargs: (
        f"INT. WRONG PLACE - DAY #{plan.scene_number}#\n\n@アリス\n修正しました。\n"
    )
    service = NarrativeAdaptationService(client, _make_test_config())

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        _verify_adaptation(service, service.adapt_text("合成原作"))


def test_narrative_adaptation_rejects_quality_issue_with_unknown_event_reference() -> None:
    client = FakeNarrativeClient()
    client.invalid_verification_reference = True
    client.verify_results[1] = [(False, "synthetic invalid reference")]

    service = NarrativeAdaptationService(client, _make_test_config())
    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        _verify_adaptation(service, service.adapt_text("合成原作"))

    assert client.revision_requests == []


def test_narrative_adaptation_service_fails_when_scene_regeneration_exhausted() -> None:
    client = FakeNarrativeClient()
    # Scene 2 fails twice (exceeds max_attempts=1)
    client.verify_results[2] = [(False, "Fail 1"), (False, "Fail 2")]
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        _verify_adaptation(
            service,
            service.adapt_text("原作本文", on_progress=lambda *_: None, ensure_not_cancelled=lambda: None),
        )


def test_narrative_adaptation_service_classify_text_and_pdf_passes_progress() -> None:
    client = FakeNarrativeClient()
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    progress_calls: list[int] = []

    def on_progress(received: int) -> None:
        progress_calls.append(received)

    kind_text = service.classify_text("テストテキスト", on_progress=on_progress)
    assert kind_text == SourceKind.NARRATIVE_PROSE
    assert progress_calls == [42]

    progress_calls.clear()
    sample_pdf = _create_synthetic_pdf(page_count=2)
    kind_pdf = service.classify_pdf(sample_pdf, on_progress=on_progress)
    assert kind_pdf == SourceKind.NARRATIVE_PROSE
    assert progress_calls == [42]


def test_narrative_adaptation_source_language_propagation() -> None:
    client = FakeNarrativeClient()
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    en_text = (
        "The river rose, and she knew that her friend must find shelter with the group. "
        "They searched the forest together and found a wooden cabin before the storm hit."
    )
    # Intermediate summaries are Japanese (FakeNarrativeClient returns "アリスが庭を歩く"),
    # but source_language must remain "en" across all stages.
    service.adapt_text(en_text, source_language="en")

    assert client.read_languages == ["en"]
    assert client.plan_languages == ["en"]
    assert all(lang == "en" for lang in client.write_languages)

    # Test Japanese source propagation
    client2 = FakeNarrativeClient()
    service2 = NarrativeAdaptationService(client2, config)
    ja_text = "川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。"
    service2.adapt_text(ja_text, source_language="ja")
    assert client2.read_languages == ["ja"]
    assert client2.plan_languages == ["ja"]
    assert all(lang == "ja" for lang in client2.write_languages)


def test_narrative_adaptation_rejects_invalid_cues_before_verify() -> None:
    client = FakeNarrativeClient()
    client.write_scene = lambda plan, context, **kwargs: (  # type: ignore[assignment]
        f"{plan.heading}\n\n@アリス stands beside @ウサギ, watching.\nThe shutter rattles.\n"
    )
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    with pytest.raises(GeneratedFountainCueError, match="^inline_at_in_cue$"):
        service.adapt_text("原作本文", source_language="ja")

    assert len(client.verified_scenes) == 0


def test_narrative_adaptation_rejects_unknown_character_cue_before_verify() -> None:
    client = FakeNarrativeClient()
    client.write_scene = lambda plan, context, **kwargs: (  # type: ignore[assignment]
        f"{plan.heading}\n\n@UNKNOWN\nHello there.\n"
    )
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    with pytest.raises(GeneratedFountainCueError, match="^unknown_character_cue$"):
        service.adapt_text("原作本文", source_language="ja")

    assert len(client.verified_scenes) == 0


def test_narrative_adaptation_rejects_speech_language_mismatch_before_verify() -> None:
    client = FakeNarrativeClient()
    client.write_scene = lambda plan, context, **kwargs: (  # type: ignore[assignment]
        f"{plan.heading}\n\n@アリス\n川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。\n"
    )
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        service.adapt_text("English source text with enough length to be confirmed.", source_language="en")

    assert len(client.verified_scenes) == 0


def test_invalid_scene_heading_is_regenerated_before_verify() -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 1 and client.write_counts[1] == 1:
            return f"INT. WRONG PLACE - DAY #{plan.scene_number}#\n\n@アリス\n合成の台詞です。\n"
        return valid

    client.write_scene = generate
    result = NarrativeAdaptationService(client, _make_test_config()).adapt_text("合成小説")

    assert client.write_counts[1] == 2
    assert result.revision_count[1] == 1
    assert result.planned_scenes[0].heading in result.scene_fountains[1]


def test_tag_only_scene_is_repaired_with_original_and_failure_reason_in_shared_budget() -> None:
    client = FakeNarrativeClient()
    write = client.write_scene
    repairs = []

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 1:
            return f"{plan.heading}\n\n@ナレーター\n<breath>\n"
        return valid

    def repair(plan, context, initial_scene, issues, story_plan, **_kwargs):
        repairs.append((context, initial_scene, issues, story_plan))
        return f"{plan.heading}\n\n@ナレーター\n合成の語りです。\n"

    client.write_scene = generate
    client.revise_scene = repair
    result = NarrativeAdaptationService(client, _make_test_config()).adapt_text("合成小説")

    assert client.write_counts[1] == 1
    assert len(repairs) == 1
    context, initial_scene, issues, story_plan = repairs[0]
    assert "<breath>" in initial_scene
    assert context.source_material
    assert story_plan is result.story_plan
    assert issues[0].issue_type == "fountain_invalid"
    assert "missing_speech" in issues[0].description
    assert issues[0].event_ids or issues[0].source_anchors
    assert result.revision_count[1] == 1
    assert "合成の語りです。" in result.scene_fountains[1]


@pytest.mark.parametrize("remains_invalid", [False, True])
def test_combined_cue_repair_uses_one_shared_revision_before_analysis(remains_invalid):
    client = FakeNarrativeClient()
    write = client.write_scene
    repairs = []

    def generate(plan, context, **kwargs):
        text = write(plan, context, **kwargs)
        if plan.scene_number == 1:
            return f"{plan.heading}\n\n@アリスとウサギ\n合成の返答。\n"
        return text

    def repair(plan, context, text, **kwargs):
        repairs.append((plan.scene_number, context, kwargs))
        return text if remains_invalid else text.replace("@アリスとウサギ", "@ナレーター")

    client.write_scene = generate
    client.repair_combined_speaker_cues = repair
    service = NarrativeAdaptationService(client, _make_test_config())
    if remains_invalid:
        with pytest.raises(NarrativeAdaptationError):
            service.adapt_text("合成小説", deadline=123.0)
    else:
        result = service.adapt_text("合成小説", deadline=123.0)
        assert result.revision_count[1] == 1
        assert "@アリスとウサギ" not in result.fountain
        client.verify_results[1] = [(False, "合成の追加指摘")]
        with pytest.raises(NarrativeAdaptationError):
            _verify_adaptation(service, result)
        assert client.revision_requests == []
    assert len(repairs) == 1
    assert repairs[0][1].source_material
    assert repairs[0][2]["deadline"] == 123.0
    assert client.write_counts[1] == 1


def test_empty_cue_is_regenerated_before_verify_with_same_context(caplog) -> None:
    client = FakeNarrativeClient()
    write = client.write_scene
    requests = []

    def generate(plan, context, **kwargs):
        requests.append((plan.scene_number, context, kwargs))
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 2 and client.write_counts[2] == 1:
            return f"{plan.heading}\n\n@アリス\n(quietly)"
        return valid

    client.write_scene = generate
    service = NarrativeAdaptationService(client, _make_test_config())
    result = _verify_adaptation(
        service,
        service.adapt_text("合成小説", deadline=1234567890.0, source_language="ja"),
    )
    assert client.write_counts[2] == 2
    assert client.verified_scenes.count(2) == 1
    scene_requests = [r for r in requests if r[0] == 2]
    assert scene_requests[0][1:] == scene_requests[1][1:]
    assert scene_requests[1][2] == {"deadline": 1234567890.0, "source_language": "ja"}
    assert "(quietly)" not in result.fountain
    assert "scene_number=2" in caplog.text
    assert "line_number=3" in caplog.text
    assert "cue_without_speech" in caplog.text
    assert "アリス" not in caplog.text


def test_empty_cue_exhaustion_is_bounded_and_never_verified(caplog) -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        write(plan, context, **kwargs)
        return f"{plan.heading}\n\n@アリス"

    client.write_scene = generate
    with pytest.raises(GeneratedFountainCueError, match="^cue_without_speech$"):
        NarrativeAdaptationService(client, _make_test_config()).adapt_text("合成小説")
    assert client.write_counts[1] == 2
    assert client.verified_scenes == []
    assert "exhausted" in caplog.text


def test_cue_and_semantic_verification_share_regeneration_budget() -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 1 and client.write_counts[1] == 1:
            return f"{plan.heading}\n\n@アリス"
        return valid

    client.write_scene = generate
    client.verify_results[1] = [(False, "synthetic verification failure")]
    service = NarrativeAdaptationService(client, _make_test_config())
    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        _verify_adaptation(service, service.adapt_text("合成小説"))
    assert client.write_counts[1] == 2


@pytest.mark.parametrize("max_attempts", [2, 3])
def test_cue_failure_during_verification_regeneration_uses_remaining_budget(max_attempts) -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        valid = write(plan, context, **kwargs)
        if plan.scene_number == 2 and client.write_counts[2] == 2:
            return f"{plan.heading}\n\n@アリス"
        return valid

    client.write_scene = generate
    client.verify_results[2] = [(False, "synthetic verification failure"), (True, None)]
    config = _make_test_config().model_copy(update={"scene_regeneration_max_attempts": max_attempts})
    service = NarrativeAdaptationService(client, config)
    adapted = service.adapt_text("合成小説")
    if max_attempts == 2:
        with pytest.raises(GeneratedFountainCueError, match="^cue_without_speech$"):
            _verify_adaptation(service, adapted)
        assert client.write_counts[2] == 2
        assert client.verified_scenes.count(2) == 1
    else:
        result = _verify_adaptation(service, adapted)
        assert "@アリス" not in result.fountain
        assert client.write_counts[2] == 3
        assert client.verified_scenes.count(2) == 2
        assert client.verified_scenes.count(3) == 2


def test_cancel_between_cue_failure_and_regeneration_prevents_next_write() -> None:
    client = FakeNarrativeClient()
    write = client.write_scene

    def generate(plan, context, **kwargs):
        write(plan, context, **kwargs)
        return f"{plan.heading}\n\n@アリス"

    def ensure_not_cancelled():
        if client.write_counts[1]:
            raise RuntimeError("synthetic cancellation")

    client.write_scene = generate
    with pytest.raises(RuntimeError, match="synthetic cancellation"):
        NarrativeAdaptationService(client, _make_test_config()).adapt_text(
            "合成小説", ensure_not_cancelled=ensure_not_cancelled
        )
    assert client.write_counts[1] == 1
    assert client.verified_scenes == []
