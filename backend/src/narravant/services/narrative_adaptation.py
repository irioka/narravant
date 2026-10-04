"""Narrative adaptation domain models, PDF page windowing, and causal plot DAG."""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from enum import StrEnum
from io import BytesIO
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader, PdfWriter

from narravant.core.content_language import (
    SourceLanguage,
    infer_source_language,
    validate_text_language,
)
from narravant.core.fountain import FountainParser
from narravant.core.generated_fountain import GeneratedFountainCueError, validate_generated_fountain_cues

logger = logging.getLogger(__name__)


class NarrativeAdaptationError(RuntimeError):
    """Raised when narrative adaptation fails or classification is inconclusive."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class SourceKind(StrEnum):
    SCREENPLAY = "SCREENPLAY"
    NARRATIVE_PROSE = "NARRATIVE_PROSE"


class PdfWindowKind(StrEnum):
    SCREENPLAY_EVIDENCE = "screenplay_evidence"
    NARRATIVE_PROSE = "narrative_prose"
    NO_STORY_CONTENT = "no_story_content"
    INCONCLUSIVE = "inconclusive"


class SourceAnchor(BaseModel):
    """Text offset or PDF page range locating content in the source document."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "pdf"]
    start_offset: int | None = None
    end_offset: int | None = None
    start_page: int | None = None
    end_page: int | None = None

    @classmethod
    def text(cls, start: int, end: int) -> SourceAnchor:
        return cls(kind="text", start_offset=start, end_offset=end)

    @classmethod
    def pdf(cls, start_page: int, end_page: int) -> SourceAnchor:
        return cls(kind="pdf", start_page=start_page, end_page=end_page)

    @property
    def start(self) -> int:
        if self.kind == "text":
            return self.start_offset if self.start_offset is not None else 0
        return self.start_page if self.start_page is not None else 1

    @property
    def end(self) -> int:
        if self.kind == "text":
            return self.end_offset if self.end_offset is not None else 0
        return self.end_page if self.end_page is not None else 1


class SourceUnit(BaseModel):
    """A bounded segment of source material assigned a stable server-managed ID."""

    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(min_length=1)
    source_anchor: SourceAnchor


class ReaderCandidateEvent(BaseModel):
    """Raw event returned from a Reader window prior to deduplication and ID assignment."""

    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    source_anchor: SourceAnchor
    character_states: dict[str, str] = Field(default_factory=dict)
    location_time: str | None = None
    foreshadowing_candidates: list[str] = Field(default_factory=list)


class StoryEvent(BaseModel):
    """Deduplicated narrative event with deterministic server-assigned ID."""

    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_anchor: SourceAnchor
    character_states: dict[str, str] = Field(default_factory=dict)
    location_time: str | None = None
    foreshadowing_candidates: list[str] = Field(default_factory=list)


class PdfPageWindow(BaseModel):
    """A bounded in-memory PDF slice for classification or reading."""

    model_config = ConfigDict(extra="forbid")
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    pdf_bytes: bytes


class PdfWindowClassification(BaseModel):
    """Classification result for a single PDF page window."""

    model_config = ConfigDict(extra="forbid")
    window: PdfPageWindow
    kind: PdfWindowKind


class ReaderWindowResult(BaseModel):
    """Reader structured output for a single PDF window."""

    model_config = ConfigDict(extra="forbid")
    window: PdfPageWindow
    candidate_events: list[ReaderCandidateEvent]
    candidate_source_units: list[SourceUnit] = Field(default_factory=list)


class MergedPdfReaderData(BaseModel):
    """Deduplicated events and source units across all Reader windows."""

    model_config = ConfigDict(extra="forbid")
    events: list[StoryEvent]
    source_units: list[SourceUnit]


CausalStrength = Literal["high", "medium", "low"]
STRENGTH_RANK: dict[CausalStrength, int] = {"high": 3, "medium": 2, "low": 1}


class CausalEdge(BaseModel):
    """Directed causal relationship between two narrative events."""

    model_config = ConfigDict(extra="forbid")
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    strength: CausalStrength = "medium"


class CausalPlotGraph(BaseModel):
    """A directed acyclic graph (DAG) of story events and causal connections."""

    model_config = ConfigDict(extra="forbid")
    events: list[StoryEvent]
    edges: list[CausalEdge]

    def breadth_first_event_ids(self) -> list[str]:
        """Linearize events level-by-level using Kahn's algorithm."""
        event_map = {event.event_id: event for event in self.events}
        in_degree: dict[str, int] = {event.event_id: 0 for event in self.events}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for edge in self.edges:
            if edge.from_node_id in in_degree and edge.to_node_id in in_degree:
                adjacency[edge.from_node_id].append(edge.to_node_id)
                in_degree[edge.to_node_id] += 1

        ordered_event_ids: list[str] = []
        current_layer = [event_id for event_id, degree in in_degree.items() if degree == 0]
        # Sort initial layer by (source position, event_id)
        current_layer.sort(key=lambda eid: (event_map[eid].source_anchor.start, eid))

        while current_layer:
            ordered_event_ids.extend(current_layer)
            next_layer: list[str] = []
            for u in current_layer:
                for v in adjacency[u]:
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        next_layer.append(v)
            next_layer.sort(key=lambda eid: (event_map[eid].source_anchor.start, eid))
            current_layer = next_layer

        # If any disconnected components or cycles remain, append unvisited nodes deterministically
        visited = set(ordered_event_ids)
        unvisited = [event.event_id for event in self.events if event.event_id not in visited]
        unvisited.sort(key=lambda eid: (event_map[eid].source_anchor.start, eid))
        ordered_event_ids.extend(unvisited)

        return ordered_event_ids


class PlannedScene(BaseModel):
    """Outline representation of a single adapted scene."""

    model_config = ConfigDict(extra="forbid")
    scene_number: int = Field(ge=1)
    heading: str = Field(min_length=1)
    source_unit_ids: list[str] = Field(min_length=1)
    retained_event_ids: list[str] = Field(min_length=1)
    omitted_event_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    causal_notes: str = Field(min_length=1)
    dependency_scene_numbers: list[int] = Field(default_factory=list)


class NarrativeAdaptationConfig(BaseModel):
    """Runtime configuration for the NarrativeAdaptationService."""

    model_config = ConfigDict(extra="forbid")
    text_reader_source_unit_max_characters: int = Field(ge=1)
    text_reader_source_unit_overlap_characters: int = Field(ge=0)
    pdf_classifier_page_window: int = Field(ge=1)
    pdf_reader_page_window: int = Field(ge=1)
    pdf_reader_page_overlap: int = Field(ge=0)
    reader_refinement_max_attempts: int = Field(ge=0)
    scene_regeneration_max_attempts: int = Field(ge=0)
    classification_max_output_tokens: int = Field(ge=1)
    reader_max_output_tokens: int = Field(ge=1)
    planning_max_output_tokens: int = Field(ge=1)
    scene_writing_max_output_tokens: int = Field(ge=1)
    verification_max_output_tokens: int = Field(ge=1)


def split_pdf_windows(source_pdf: bytes, page_window: int, overlap: int) -> list[PdfPageWindow]:
    """Slice a PDF into in-memory page windows without invoking text extraction."""
    reader = PdfReader(BytesIO(source_pdf), strict=True)
    total_pages = len(reader.pages)
    step = page_window - overlap
    if step <= 0:
        raise ValueError("overlap must be less than page_window")

    windows: list[PdfPageWindow] = []
    start_idx = 0
    while start_idx < total_pages:
        end_idx = min(start_idx + page_window, total_pages)
        writer = PdfWriter()
        for p in range(start_idx, end_idx):
            writer.add_page(reader.pages[p])
        buffer = BytesIO()
        writer.write(buffer)
        windows.append(
            PdfPageWindow(
                start_page=start_idx + 1,
                end_page=end_idx,
                pdf_bytes=buffer.getvalue(),
            )
        )
        if end_idx >= total_pages:
            break
        start_idx += step

    return windows


def aggregate_pdf_classifications(
    results: Sequence[PdfWindowClassification],
) -> SourceKind:
    """Screenplay-First aggregation of window classifications."""
    if not results:
        raise NarrativeAdaptationError("SOURCE_CLASSIFICATION_INCONCLUSIVE")

    if any(r.kind == PdfWindowKind.SCREENPLAY_EVIDENCE for r in results):
        return SourceKind.SCREENPLAY

    has_narrative = any(r.kind == PdfWindowKind.NARRATIVE_PROSE for r in results)
    all_allowed = all(r.kind in (PdfWindowKind.NARRATIVE_PROSE, PdfWindowKind.NO_STORY_CONTENT) for r in results)

    if has_narrative and all_allowed:
        return SourceKind.NARRATIVE_PROSE

    raise NarrativeAdaptationError("SOURCE_CLASSIFICATION_INCONCLUSIVE")


def normalize_event_summary_key(summary: str) -> str:
    """Derive a normalized comparison key: Unicode NFKC -> casefold -> collapse whitespace."""
    nfkc = unicodedata.normalize("NFKC", summary).casefold()
    return " ".join(nfkc.split())


def merge_pdf_reader_candidates(
    results: Sequence[ReaderWindowResult],
) -> MergedPdfReaderData:
    """Merge reader window outputs by window ownership, anchor deduplication, and normalized event key."""
    # A candidate unit or event is owned by the FIRST reader window that fully contains its anchor.
    all_windows = [r.window for r in results]

    # Collect candidate units and candidate events
    retained_events_by_window: list[tuple[PdfPageWindow, ReaderCandidateEvent]] = []
    retained_units_by_window: list[tuple[PdfPageWindow, SourceUnit]] = []

    for res in results:
        w = res.window
        for candidate_unit in res.candidate_source_units:
            anchor = candidate_unit.source_anchor
            if anchor.start_page is not None and anchor.end_page is not None:
                first_containing = next(
                    (
                        win
                        for win in all_windows
                        if win.start_page <= anchor.start_page and anchor.end_page <= win.end_page
                    ),
                    None,
                )
                if first_containing == w:
                    retained_units_by_window.append((w, candidate_unit))

        for candidate_event in res.candidate_events:
            anchor = candidate_event.source_anchor
            if anchor.start_page is not None and anchor.end_page is not None:
                first_containing = next(
                    (
                        win
                        for win in all_windows
                        if win.start_page <= anchor.start_page and anchor.end_page <= win.end_page
                    ),
                    None,
                )
                if first_containing == w:
                    retained_events_by_window.append((w, candidate_event))

    # Deduplicate events:
    # Sort key: (window.start_page, anchor.start, anchor.end, event_key)
    event_sort_tuples: list[tuple[int, int, int, str, ReaderCandidateEvent]] = []
    for w, cand in retained_events_by_window:
        norm_key = normalize_event_summary_key(cand.summary)
        event_sort_tuples.append(
            (
                w.start_page,
                cand.source_anchor.start,
                cand.source_anchor.end,
                norm_key,
                cand,
            )
        )

    event_sort_tuples.sort(key=lambda t: (t[0], t[1], t[2], t[3]))

    seen_event_keys: set[tuple[int, int, str]] = set()
    deduped_events: list[StoryEvent] = []
    for _w_start, a_start, a_end, norm_key, cand in event_sort_tuples:
        dedup_key = (a_start, a_end, norm_key)
        if dedup_key in seen_event_keys:
            continue
        seen_event_keys.add(dedup_key)
        event_id = f"ev_{len(deduped_events) + 1:04d}"
        deduped_events.append(
            StoryEvent(
                event_id=event_id,
                summary=cand.summary,
                source_anchor=cand.source_anchor,
                character_states=cand.character_states,
                location_time=cand.location_time,
                foreshadowing_candidates=cand.foreshadowing_candidates,
            )
        )

    # Deduplicate source units:
    seen_unit_anchors: set[tuple[int, int]] = set()
    deduped_units: list[SourceUnit] = []
    for _w, unit in retained_units_by_window:
        unit_key = (unit.source_anchor.start, unit.source_anchor.end)
        if unit_key in seen_unit_anchors:
            continue
        seen_unit_anchors.add(unit_key)
        unit_id = f"su_{len(deduped_units) + 1:04d}"
        deduped_units.append(SourceUnit(unit_id=unit_id, source_anchor=unit.source_anchor))

    return MergedPdfReaderData(events=deduped_events, source_units=deduped_units)


def build_causal_dag(events: Sequence[StoryEvent], candidate_edges: Sequence[CausalEdge]) -> CausalPlotGraph:
    """Construct a cycle-free CausalPlotGraph deterministically."""
    event_map = {event.event_id: event for event in events}
    event_ids = set(event_map.keys())

    # 1. Filter valid edges referencing known nodes without self-loops
    valid_candidates: list[CausalEdge] = []
    seen_edge_pairs: dict[tuple[str, str], CausalEdge] = {}
    for edge in candidate_edges:
        if edge.from_node_id in event_ids and edge.to_node_id in event_ids and edge.from_node_id != edge.to_node_id:
            pair = (edge.from_node_id, edge.to_node_id)
            if pair in seen_edge_pairs:
                # Keep higher strength if duplicated
                if STRENGTH_RANK[edge.strength] > STRENGTH_RANK[seen_edge_pairs[pair].strength]:
                    seen_edge_pairs[pair] = edge
            else:
                seen_edge_pairs[pair] = edge
    valid_candidates = list(seen_edge_pairs.values())

    # 2. Compute fixed initial_degree (in + out) for each node before cycle breaking
    initial_degree: dict[str, int] = defaultdict(int)
    for edge in valid_candidates:
        initial_degree[edge.from_node_id] += 1
        initial_degree[edge.to_node_id] += 1

    # 3. Sort candidate edges deterministically:
    # (-strength_rank, initial_degree[from] + initial_degree[to], from.source_anchor.start, from_id, to_id)
    sorted_edges = sorted(
        valid_candidates,
        key=lambda e: (
            -STRENGTH_RANK[e.strength],
            initial_degree[e.from_node_id] + initial_degree[e.to_node_id],
            event_map[e.from_node_id].source_anchor.start,
            e.from_node_id,
            e.to_node_id,
        ),
    )

    # 4. Cycle-breaking using reachability check
    accepted_edges: list[CausalEdge] = []
    adjacency: dict[str, set[str]] = defaultdict(set)

    def is_reachable(source: str, target: str) -> bool:
        if source == target:
            return True
        visited = set()
        queue = deque([source])
        while queue:
            node = queue.popleft()
            if node == target:
                return True
            for nxt in adjacency[node]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False

    for edge in sorted_edges:
        # If to_node_id can already reach from_node_id, adding from -> to creates a cycle!
        if not is_reachable(edge.to_node_id, edge.from_node_id):
            accepted_edges.append(edge)
            adjacency[edge.from_node_id].add(edge.to_node_id)

    return CausalPlotGraph(events=list(events), edges=accepted_edges)


class NarrativeGenerationClient(Protocol):
    """Protocol defining model generation stages for narrative adaptation."""

    def classify_text_source(
        self,
        text: str,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> SourceKind: ...

    def classify_pdf_window(
        self,
        window: PdfPageWindow,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> PdfWindowClassification: ...

    def read_text_window(
        self,
        text_window: str,
        source_anchor: SourceAnchor,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> list[ReaderCandidateEvent]: ...

    def read_pdf_window(
        self,
        window: PdfPageWindow,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> ReaderWindowResult: ...

    def build_causal_edges(
        self,
        events: Sequence[StoryEvent],
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> list[CausalEdge]: ...

    def plan_scenes(
        self,
        dag: CausalPlotGraph,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> list[PlannedScene]: ...

    def write_scene(
        self,
        scene_plan: PlannedScene,
        source_units: Sequence[SourceUnit],
        previous_scenes: Sequence[str],
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> str: ...

    def verify_scene(
        self,
        scene_plan: PlannedScene,
        scene_text: str,
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> tuple[bool, str | None]: ...

    def normalize_screenplay_text(
        self,
        text: str,
        on_progress: Callable[[int], None] | None = None,
        *,
        deadline: float | None = None,
    ) -> str: ...

    def normalize_screenplay_pdf(
        self,
        source_pdf: bytes,
        on_progress: Callable[[int], None] | None = None,
        *,
        deadline: float | None = None,
    ) -> str: ...


class NarrativeAdaptationService:
    """Coordinates classification, Reading, DAG construction, Planning, Writing, and Verification."""

    def __init__(self, client: NarrativeGenerationClient, config: NarrativeAdaptationConfig) -> None:
        self.client = client
        self.config = config

    def classify_text(
        self,
        source_text: str,
        *,
        on_progress: Callable[[int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> SourceKind:
        if ensure_not_cancelled:
            ensure_not_cancelled()
        return self.client.classify_text_source(
            source_text,
            on_progress=on_progress,
            deadline=deadline,
        )

    def classify_pdf(
        self,
        source_pdf: bytes,
        *,
        on_progress: Callable[[int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> SourceKind:
        if ensure_not_cancelled:
            ensure_not_cancelled()
        windows = split_pdf_windows(
            source_pdf,
            page_window=self.config.pdf_classifier_page_window,
            overlap=0,
        )
        classifications: list[PdfWindowClassification] = []
        for window in windows:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            cls_result = self.client.classify_pdf_window(
                window,
                on_progress=on_progress,
                deadline=deadline,
            )
            classifications.append(cls_result)
        return aggregate_pdf_classifications(classifications)

    def adapt_text(
        self,
        source_text: str,
        *,
        on_progress: Callable[[str, int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> str:
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("reading", 0)

        if source_language == "und" and source_text:
            source_language = infer_source_language(source_text)

        max_chars = self.config.text_reader_source_unit_max_characters
        overlap = self.config.text_reader_source_unit_overlap_characters
        step = max_chars - overlap

        text_len = len(source_text)
        windows: list[tuple[int, int, str]] = []
        start = 0
        while start < text_len:
            end = min(start + max_chars, text_len)
            windows.append((start, end, source_text[start:end]))
            if end >= text_len:
                break
            start += step

        raw_events: list[ReaderCandidateEvent] = []
        source_units: list[SourceUnit] = []
        for i, (w_start, w_end, w_text) in enumerate(windows):
            if ensure_not_cancelled:
                ensure_not_cancelled()
            anchor = SourceAnchor.text(w_start, w_end)
            unit_id = f"su_{i + 1:04d}"
            source_units.append(SourceUnit(unit_id=unit_id, source_anchor=anchor))

            events = self._read_text_with_refinement(
                w_text,
                anchor,
                ensure_not_cancelled=ensure_not_cancelled,
                deadline=deadline,
                source_language=source_language,
            )
            raw_events.extend(events)

        if not raw_events:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        events_by_key: dict[tuple[int, int, str], StoryEvent] = {}
        for cand in raw_events:
            key = (
                cand.source_anchor.start,
                cand.source_anchor.end,
                normalize_event_summary_key(cand.summary),
            )
            if key not in events_by_key:
                event_id = f"ev_{len(events_by_key) + 1:04d}"
                events_by_key[key] = StoryEvent(
                    event_id=event_id,
                    summary=cand.summary,
                    source_anchor=cand.source_anchor,
                    character_states=cand.character_states,
                    location_time=cand.location_time,
                    foreshadowing_candidates=cand.foreshadowing_candidates,
                )
            story_events = list(events_by_key.values())
        return self._run_pipeline(
            story_events,
            source_units,
            on_progress,
            ensure_not_cancelled,
            deadline,
            source_language=source_language,
        )

    def adapt_pdf(
        self,
        source_pdf: bytes,
        *,
        on_progress: Callable[[str, int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> str:
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("reading", 0)

        windows = split_pdf_windows(
            source_pdf,
            page_window=self.config.pdf_reader_page_window,
            overlap=self.config.pdf_reader_page_overlap,
        )
        reader_results: list[ReaderWindowResult] = []
        for window in windows:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            result = self.client.read_pdf_window(window, deadline=deadline)
            reader_results.append(result)

        merged = merge_pdf_reader_candidates(reader_results)
        if not merged.events:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        return self._run_pipeline(
            merged.events,
            merged.source_units,
            on_progress,
            ensure_not_cancelled,
            deadline,
        )

    def _read_text_with_refinement(
        self,
        text_window: str,
        source_anchor: SourceAnchor,
        *,
        ensure_not_cancelled: Callable[[], None] | None,
        deadline: float | None,
        source_language: SourceLanguage = "und",
    ) -> list[ReaderCandidateEvent]:
        for attempt in range(self.config.reader_refinement_max_attempts + 1):
            if ensure_not_cancelled:
                ensure_not_cancelled()
            events = self.client.read_text_window(
                text_window,
                source_anchor,
                deadline=deadline,
                source_language=source_language,
            )
            valid = True
            for ev in events:
                if not ev.summary or not ev.summary.strip():
                    valid = False
                    break
                if ev.source_anchor.kind != "text":
                    valid = False
                    break
            if valid and events:
                return events
            if attempt >= self.config.reader_refinement_max_attempts:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        return []

    def _run_pipeline(
        self,
        story_events: list[StoryEvent],
        source_units: list[SourceUnit],
        on_progress: Callable[[str, int], None] | None,
        ensure_not_cancelled: Callable[[], None] | None,
        deadline: float | None,
        *,
        source_language: SourceLanguage = "und",
    ) -> str:
        # 2. Causal Plot Graph
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("building_plot_graph", 0)

        candidate_edges = self.client.build_causal_edges(story_events, deadline=deadline)
        dag = build_causal_dag(story_events, candidate_edges)

        # 3. Planning
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("planning", 0)

        planned_scenes = self.client.plan_scenes(dag, deadline=deadline, source_language=source_language)
        if not planned_scenes:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        planned_scenes.sort(key=lambda s: s.scene_number)

        # 4. Writing
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("writing", 0)

        def _validate_scene_piece(piece: str, characters: list[str]) -> None:
            validate_generated_fountain_cues(
                piece,
                known_source_speakers=characters,
                strict_speaker_names=True,
            )
            parsed = FountainParser.parse(piece)
            utterance_text = "\n".join(item.text for item in parsed.all_utterances())
            validate_text_language(utterance_text, source_language)

        # 構造検査・verify・後続場面の改稿で同じ場面別予算を共有する。
        regeneration_attempts: dict[int, int] = defaultdict(int)

        def _reserve_regeneration(scene_number: int) -> None:
            if regeneration_attempts[scene_number] >= self.config.scene_regeneration_max_attempts:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            regeneration_attempts[scene_number] += 1

        def _write_valid_scene(plan: PlannedScene, previous: list[str]) -> str:
            while True:
                if ensure_not_cancelled:
                    ensure_not_cancelled()
                piece = self.client.write_scene(
                    plan, source_units, previous, deadline=deadline, source_language=source_language
                )
                try:
                    _validate_scene_piece(piece, plan.characters)
                    return piece
                except GeneratedFountainCueError as exc:
                    exhausted = regeneration_attempts[plan.scene_number] >= self.config.scene_regeneration_max_attempts
                    logger.warning(
                        "Generated scene cue invalid; %s scene_number=%d line_number=%s reason=%s "
                        "regeneration_attempts=%d regeneration_max_attempts=%d",
                        "attempts exhausted" if exhausted else "regenerating",
                        plan.scene_number,
                        exc.line_number,
                        exc.reason,
                        regeneration_attempts[plan.scene_number],
                        self.config.scene_regeneration_max_attempts,
                    )
                    if exhausted:
                        raise
                    _reserve_regeneration(plan.scene_number)

        scene_fountains: dict[int, str] = {}
        for plan in planned_scenes:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            prev_scenes = [
                scene_fountains[p.scene_number] for p in planned_scenes if p.scene_number < plan.scene_number
            ]
            fountain_piece = _write_valid_scene(plan, prev_scenes)
            scene_fountains[plan.scene_number] = fountain_piece

        # 5. Verifying & Selective Regeneration
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("verifying", 0)

        scene_map = {plan.scene_number: plan for plan in planned_scenes}
        dependency_graph: dict[int, list[int]] = defaultdict(list)
        for plan in planned_scenes:
            for dep in plan.dependency_scene_numbers:
                dependency_graph[dep].append(plan.scene_number)

        def get_downstream_scenes(scene_num: int) -> list[int]:
            visited = set()
            queue = deque([scene_num])
            downstream = []
            while queue:
                curr = queue.popleft()
                for nxt in dependency_graph[curr]:
                    if nxt not in visited:
                        visited.add(nxt)
                        downstream.append(nxt)
                        queue.append(nxt)
            downstream.sort()
            return downstream

        # First pass: verify all scenes in topological order
        initial_failed_scenes: list[int] = []
        for plan in planned_scenes:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            passed, _reason = self.client.verify_scene(plan, scene_fountains[plan.scene_number], deadline=deadline)
            if not passed:
                initial_failed_scenes.append(plan.scene_number)

        reverification_queue: deque[int] = deque()
        in_queue: set[int] = set()

        for s_num in initial_failed_scenes:
            plan = scene_map[s_num]
            _reserve_regeneration(s_num)
            prev_scenes = [
                scene_fountains[p.scene_number] for p in planned_scenes if p.scene_number < plan.scene_number
            ]
            new_text = _write_valid_scene(plan, prev_scenes)
            scene_fountains[s_num] = new_text

            re_passed, _re_reason = self.client.verify_scene(plan, new_text, deadline=deadline)
            if not re_passed:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

            for down_s in get_downstream_scenes(s_num):
                if down_s not in in_queue:
                    reverification_queue.append(down_s)
                    in_queue.add(down_s)

        while reverification_queue:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            s_num = reverification_queue.popleft()
            in_queue.discard(s_num)
            plan = scene_map[s_num]
            current_text = scene_fountains[s_num]

            passed, _reason = self.client.verify_scene(plan, current_text, deadline=deadline)
            if not passed:
                _reserve_regeneration(s_num)
                prev_scenes = [
                    scene_fountains[p.scene_number] for p in planned_scenes if p.scene_number < plan.scene_number
                ]
                new_text = _write_valid_scene(plan, prev_scenes)
                scene_fountains[s_num] = new_text

                re_passed, _re_reason = self.client.verify_scene(plan, new_text, deadline=deadline)
                if not re_passed:
                    raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

                for down_s in get_downstream_scenes(s_num):
                    if down_s not in in_queue:
                        reverification_queue.append(down_s)
                        in_queue.add(down_s)

        full_fountain = "\n".join(scene_fountains[plan.scene_number].strip() for plan in planned_scenes) + "\n"
        return full_fountain
