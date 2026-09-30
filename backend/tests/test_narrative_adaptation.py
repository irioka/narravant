"""Synthetic tests for in-memory narrative adaptation, PDF windowing, and causal DAG."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from io import BytesIO
from unittest.mock import patch

import pytest
from pypdf import PageObject, PdfWriter

from narravant.services.narrative_adaptation import (
    CausalEdge,
    CausalPlotGraph,
    MergedPdfReaderData,
    NarrativeAdaptationConfig,
    NarrativeAdaptationError,
    NarrativeAdaptationService,
    PdfPageWindow,
    PdfWindowClassification,
    PdfWindowKind,
    PlannedScene,
    ReaderCandidateEvent,
    ReaderWindowResult,
    SourceAnchor,
    SourceKind,
    SourceUnit,
    StoryEvent,
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

    # Event in page 6 fully contained in window 1 (and window 2 starts at 6)
    # Window 1 is the first window containing the anchor (6, 6), so w1 owns it.
    e_page6_w1 = ReaderCandidateEvent(
        summary="主人公が 鍵を 発見する。\t",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=6),
    )
    # Window 2 extracts the same event on page 6 (with different whitespace/case)
    e_page6_w2 = ReaderCandidateEvent(
        summary="主人公が  鍵を発見する。 ",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=6),
    )
    # Event spanning page 6 to 7: window 1 does NOT contain anchor.end_page=7,
    # so window 2 owns it!
    e_page6_7_w2 = ReaderCandidateEvent(
        summary="部屋を出る",
        source_anchor=SourceAnchor.pdf(start_page=6, end_page=7),
    )

    r1 = ReaderWindowResult(window=w1, candidate_events=[e_page6_w1])
    r2 = ReaderWindowResult(window=w2, candidate_events=[e_page6_w2, e_page6_7_w2])

    merged: MergedPdfReaderData = merge_pdf_reader_candidates([r1, r2])

    # Only 2 events should remain (duplicate on page 6 dropped)
    assert len(merged.events) == 2
    p6_events = [e for e in merged.events if e.source_anchor.start_page == 6 and e.source_anchor.end_page == 6]
    assert len(p6_events) == 1

    p6_7_events = [e for e in merged.events if e.source_anchor.start_page == 6 and e.source_anchor.end_page == 7]
    assert len(p6_7_events) == 1

    # Ensure event IDs are generated deterministically and are valid
    assert all(e.event_id.startswith("ev_") for e in merged.events)


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
        self.write_counts: dict[int, int] = defaultdict(int)
        self.verified_scenes: list[int] = []

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
    ) -> list[ReaderCandidateEvent]:
        return [
            ReaderCandidateEvent(
                summary="アリスが庭を歩く",
                source_anchor=SourceAnchor.text(0, 100),
            ),
            ReaderCandidateEvent(
                summary="ウサギが走る",
                source_anchor=SourceAnchor.text(100, 200),
            ),
        ]

    def read_pdf_window(self, window: PdfPageWindow, on_progress=None, deadline=None) -> ReaderWindowResult:
        return ReaderWindowResult(
            window=window,
            candidate_events=[
                ReaderCandidateEvent(
                    summary="アリスが庭を歩く",
                    source_anchor=SourceAnchor.pdf(window.start_page, window.start_page),
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

    def plan_scenes(self, dag: CausalPlotGraph, on_progress=None, deadline=None) -> list[PlannedScene]:
        return [
            PlannedScene(
                scene_number=1,
                heading="EXT. GARDEN - DAY #1#",
                source_unit_ids=["su_0001"],
                retained_event_ids=["ev_0001"],
                purpose="日常の提示",
                characters=["アリス"],
                causal_notes="始まり",
            ),
            PlannedScene(
                scene_number=2,
                heading="EXT. PATH - DAY #2#",
                source_unit_ids=["su_0001"],
                retained_event_ids=["ev_0002"],
                purpose="事件発生",
                characters=["アリス", "ウサギ"],
                causal_notes="追いかける",
                dependency_scene_numbers=[1],
            ),
            PlannedScene(
                scene_number=3,
                heading="INT. RABBIT HOLE - DAY #3#",
                source_unit_ids=["su_0001"],
                retained_event_ids=["ev_0002"],
                purpose="落下",
                characters=["アリス"],
                causal_notes="穴へ落ちる",
                dependency_scene_numbers=[2],
            ),
        ]

    def write_scene(
        self,
        scene_plan: PlannedScene,
        source_units: Sequence[SourceUnit],
        previous_scenes: Sequence[str],
        on_progress=None,
        deadline=None,
    ) -> str:
        self.write_counts[scene_plan.scene_number] += 1
        return (
            f"{scene_plan.heading}\n\n"
            f"Action for scene {scene_plan.scene_number} (v{self.write_counts[scene_plan.scene_number]}).\n"
        )

    def verify_scene(
        self, scene_plan: PlannedScene, scene_text: str, on_progress=None, deadline=None
    ) -> tuple[bool, str | None]:
        self.verified_scenes.append(scene_plan.scene_number)
        results = self.verify_results.get(scene_plan.scene_number)
        if results:
            return results.pop(0)
        return True, None

    def normalize_screenplay_text(self, text: str, on_progress=None, deadline=None) -> str:
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
        reader_refinement_max_attempts=1,
        scene_regeneration_max_attempts=1,
        classification_max_output_tokens=2048,
        reader_max_output_tokens=8192,
        planning_max_output_tokens=16384,
        scene_writing_max_output_tokens=16384,
        verification_max_output_tokens=4096,
    )


def test_narrative_adaptation_service_adapts_text_prose_successfully() -> None:
    client = FakeNarrativeClient()
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    phases_reported: list[str] = []

    def on_progress(phase: str, _count: int) -> None:
        phases_reported.append(phase)

    fountain = service.adapt_text(
        "原作小説の長いテキストです。",
        on_progress=on_progress,
        ensure_not_cancelled=lambda: None,
    )

    assert "EXT. GARDEN - DAY #1#" in fountain
    assert "EXT. PATH - DAY #2#" in fountain
    assert "INT. RABBIT HOLE - DAY #3#" in fountain
    assert "reading" in phases_reported
    assert "building_plot_graph" in phases_reported
    assert "planning" in phases_reported
    assert "writing" in phases_reported
    assert "verifying" in phases_reported


def test_narrative_adaptation_service_retries_rejected_scene_and_reverifies_downstream() -> None:
    client = FakeNarrativeClient()
    # Scene 2 fails first time, passes on retry.
    # Scene 3 depends on scene 2, so after scene 2 is regenerated and passes,
    # scene 3 must be reverified!
    client.verify_results[2] = [(False, "Fountain syntax error"), (True, None)]
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    service.adapt_text("原作本文", on_progress=lambda *_: None, ensure_not_cancelled=lambda: None)

    # Scene 2 was written twice
    assert client.write_counts[2] == 2
    # Scene 2 was verified twice
    assert client.verified_scenes.count(2) == 2
    # Scene 3 was verified at least twice (initial pass + downstream re-verification)
    assert client.verified_scenes.count(3) == 2


def test_narrative_adaptation_service_fails_when_scene_regeneration_exhausted() -> None:
    client = FakeNarrativeClient()
    # Scene 2 fails twice (exceeds max_attempts=1)
    client.verify_results[2] = [(False, "Fail 1"), (False, "Fail 2")]
    config = _make_test_config()
    service = NarrativeAdaptationService(client, config)

    with pytest.raises(NarrativeAdaptationError, match="ADAPTATION_VERIFICATION_FAILED"):
        service.adapt_text("原作本文", on_progress=lambda *_: None, ensure_not_cancelled=lambda: None)


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
