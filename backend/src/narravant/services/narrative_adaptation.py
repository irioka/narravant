"""Narrative adaptation domain models, PDF page windowing, and causal plot DAG."""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from io import BytesIO
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf import PdfReader, PdfWriter

from narravant.core.content_language import (
    ContentLanguageMismatch,
    SourceLanguage,
    infer_source_language,
    validate_text_language,
)
from narravant.core.fountain import FountainParser
from narravant.core.generated_fountain import (
    GeneratedFountainCueError,
    combined_speaker_passages,
    validate_generated_fountain_cues,
)

logger = logging.getLogger(__name__)


class NarrativeAdaptationError(RuntimeError):
    """Raised when narrative adaptation fails or classification is inconclusive."""

    def __init__(
        self, code: str, message: str | None = None, *, context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.context = context

    @classmethod
    def scene_failure(
        cls, reason: str, scene_number: int, revision_count: int, max_retries: int,
        issues: Sequence[QualityIssue] = (),
    ) -> NarrativeAdaptationError:
        """Return a displayable stop reason without source text or free-form descriptions."""
        return cls(
            "ADAPTATION_VERIFICATION_FAILED",
            context={
                "reason": reason,
                "scene_number": scene_number,
                "revision_count": revision_count,
                "max_retries": max_retries,
                "issue_types": sorted({issue.issue_type for issue in issues}),
            },
        )


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
    character_traits: dict[str, str] = Field(default_factory=dict)
    knowledge_changes: dict[str, str] = Field(default_factory=dict)
    relationship_changes: dict[str, str] = Field(default_factory=dict)
    location_time: str | None = None
    foreshadowing_candidates: list[str] = Field(default_factory=list)


class StoryEvent(BaseModel):
    """Deduplicated narrative event with deterministic server-assigned ID."""

    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_anchor: SourceAnchor
    character_states: dict[str, str] = Field(default_factory=dict)
    character_traits: dict[str, str] = Field(default_factory=dict)
    knowledge_changes: dict[str, str] = Field(default_factory=dict)
    relationship_changes: dict[str, str] = Field(default_factory=dict)
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
    omitted_event_reasons: dict[str, str] = Field(default_factory=dict)
    purpose: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    causal_notes: str = Field(min_length=1)
    dependency_scene_numbers: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def omitted_events_have_reasons(self) -> PlannedScene:
        if set(self.omitted_event_reasons) != set(self.omitted_event_ids):
            raise ValueError("omitted_event_reasons_must_match_omitted_event_ids")
        if any(not reason.strip() for reason in self.omitted_event_reasons.values()):
            raise ValueError("omitted_event_reason_required")
        return self


SOURCE_TURNING_POINT_LABELS = {
    1: "Opportunity",
    2: "Change of Plans",
    3: "Point of No Return",
    4: "Major Setback",
    5: "Climax",
}


class SourceTurningPoint(BaseModel):
    """原作上の転換点。完成稿の scene 番号付き分析とは別の内部計画。"""

    model_config = ConfigDict(extra="forbid")
    tp_number: int = Field(ge=1, le=5)
    label: str = Field(min_length=1)
    availability: Literal["identified", "not_applicable"]
    event_ids: list[str] = Field(default_factory=list)
    evidence: list[SourceAnchor] = Field(default_factory=list)
    change: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> SourceTurningPoint:
        if self.label != SOURCE_TURNING_POINT_LABELS[self.tp_number]:
            raise ValueError("source_turning_point_label_mismatch")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("duplicate_source_turning_point_event_id")
        if self.availability == "identified":
            if not self.event_ids or not self.evidence or not self.change or self.reason is not None:
                raise ValueError("identified_source_turning_point_requires_evidence")
        elif self.event_ids or self.evidence or self.change or not (self.reason and self.reason.strip()):
            raise ValueError("not_applicable_source_turning_point_requires_reason")
        return self


class CharacterKnowledge(BaseModel):
    """原作由来の恒常的特徴と、イベント単位の知識・関係・状態を分けて保持する。"""

    model_config = ConfigDict(extra="forbid")
    character_name: str = Field(min_length=1)
    evidence_event_ids: list[str] = Field(min_length=1)
    defining_traits: list[str] = Field(default_factory=list)
    state_by_event: dict[str, str] = Field(default_factory=dict)
    knowledge_by_event: dict[str, str] = Field(default_factory=dict)
    relationship_by_event: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event_evidence(self) -> CharacterKnowledge:
        if (
            len(set(self.evidence_event_ids)) != len(self.evidence_event_ids)
            or not set(self.state_by_event) <= set(self.evidence_event_ids)
            or not set(self.knowledge_by_event) <= set(self.evidence_event_ids)
            or not set(self.relationship_by_event) <= set(self.evidence_event_ids)
        ):
            raise ValueError("character_knowledge_event_evidence_mismatch")
        return self


@dataclass(frozen=True)
class SourceCharacterCard:
    """声の特徴に使う、原作で裏付けられた恒常的な人物情報だけを保持する。"""

    character_name: str
    defining_traits: tuple[str, ...]


class QualityIssue(BaseModel):
    """原文・保持条件に紐づく、場面内で修正可能な品質指摘。"""

    model_config = ConfigDict(extra="forbid")
    scene_number: int = Field(ge=1)
    issue_type: Literal[
        "event_omission",
        "motivation_change",
        "knowledge_boundary",
        "speaker_attribution",
        "ending_change",
        "foreshadowing_mismatch",
        "source_contradiction",
        "fountain_invalid",
    ]
    severity: Literal["major", "minor"]
    event_ids: list[str] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    preservation_condition: str = Field(min_length=1)
    description: str = Field(min_length=1)
    revision_direction: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grounding(self) -> QualityIssue:
        if not self.event_ids and not self.source_anchors:
            raise ValueError("quality_issue_requires_source_evidence")
        if any(not event_id.strip() for event_id in self.event_ids):
            raise ValueError("quality_issue_event_ids_must_be_non_empty")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("quality_issue_event_ids_must_be_unique")
        if len({anchor.model_dump_json() for anchor in self.source_anchors}) != len(self.source_anchors):
            raise ValueError("quality_issue_source_anchors_must_be_unique")
        return self


class ForeshadowingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup_event_id: str = Field(min_length=1)
    payoff_event_id: str | None = None
    description: str = Field(min_length=1)


class StoryPlan(BaseModel):
    """原作の出来事・転換点・動機・主題・結末と提示順を持つ一時計画。"""

    model_config = ConfigDict(extra="forbid")
    turning_points: list[SourceTurningPoint] = Field(min_length=5, max_length=5)
    important_event_ids: list[str] = Field(min_length=1)
    character_motivations: dict[str, str] = Field(default_factory=dict)
    central_theme: str = Field(min_length=1)
    intended_outcome: str = Field(min_length=1)
    foreshadowings: list[ForeshadowingPlan] = Field(default_factory=list)
    presentation_order_event_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_turning_point_order(self) -> StoryPlan:
        if [point.tp_number for point in self.turning_points] != [1, 2, 3, 4, 5]:
            raise ValueError("source_turning_points_must_be_ordered_1_to_5")
        if len(set(self.important_event_ids)) != len(self.important_event_ids):
            raise ValueError("duplicate_important_event_id")
        if len(set(self.presentation_order_event_ids)) != len(self.presentation_order_event_ids):
            raise ValueError("duplicate_presentation_order_event_id")
        return self


class NarrativePlan(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    story_plan: StoryPlan
    character_knowledge: list[CharacterKnowledge]
    planned_scenes: list[PlannedScene]


@dataclass(frozen=True)
class ResolvedSourceMaterial:
    """An exact in-memory text span or PDF page range resolved from the original source."""

    source_anchor: SourceAnchor
    content: str | bytes = field(repr=False)


@dataclass(frozen=True)
class SceneContext:
    """原文、恒常的な人物カード、場面までの状態、継続に必要な先行場面を保持する。"""

    scene_number: int
    source_unit_ids: tuple[str, ...]
    retained_events: tuple[StoryEvent, ...]
    source_material: tuple[ResolvedSourceMaterial, ...] = field(repr=False)
    character_knowledge: tuple[CharacterKnowledge, ...] = field(default=(), repr=False)
    unresolved_foreshadowings: tuple[ForeshadowingPlan, ...] = field(default=(), repr=False)
    related_previous_scenes: tuple[tuple[int, str], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class SourceContext:
    """In-memory original source and its server-managed source-unit index."""

    source_text: str | None = field(repr=False)
    source_pdf: bytes | None = field(repr=False)
    source_units: tuple[SourceUnit, ...]
    page_count: int | None = None

    def __post_init__(self) -> None:
        if (self.source_text is None) == (self.source_pdf is None):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        if self.source_text is not None:
            if self.page_count is not None:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        else:
            try:
                actual_page_count = len(PdfReader(BytesIO(self.source_pdf or b""), strict=True).pages)
            except Exception as exc:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED") from exc
            if actual_page_count < 1 or self.page_count not in (None, actual_page_count):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            object.__setattr__(self, "page_count", actual_page_count)

        unit_ids: set[str] = set()
        for unit in self.source_units:
            if not unit.unit_id.strip() or unit.unit_id in unit_ids:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            unit_ids.add(unit.unit_id)
            self._validate_anchor(unit.source_anchor)

    @classmethod
    def from_text(cls, source_text: str, source_units: Sequence[SourceUnit]) -> SourceContext:
        return cls(source_text=source_text, source_pdf=None, source_units=tuple(source_units))

    @classmethod
    def from_pdf(cls, source_pdf: bytes, source_units: Sequence[SourceUnit]) -> SourceContext:
        return cls(source_text=None, source_pdf=source_pdf, source_units=tuple(source_units))

    def _validate_anchor(self, anchor: SourceAnchor) -> None:
        if anchor.kind == "text":
            if (
                self.source_text is None
                or anchor.start_offset is None
                or anchor.end_offset is None
                or anchor.start_page is not None
                or anchor.end_page is not None
                or anchor.start_offset < 0
                or anchor.end_offset <= anchor.start_offset
                or anchor.end_offset > len(self.source_text)
            ):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            return

        if (
            self.source_pdf is None
            or self.page_count is None
            or anchor.start_page is None
            or anchor.end_page is None
            or anchor.start_offset is not None
            or anchor.end_offset is not None
            or anchor.start_page < 1
            or anchor.end_page < anchor.start_page
            or anchor.end_page > self.page_count
        ):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    @staticmethod
    def _contains(outer: SourceAnchor, inner: SourceAnchor) -> bool:
        return outer.kind == inner.kind and outer.start <= inner.start and inner.end <= outer.end

    @staticmethod
    def _merge_anchors(anchors: Sequence[SourceAnchor]) -> tuple[SourceAnchor, ...]:
        ordered = sorted(anchors, key=lambda anchor: (anchor.start, anchor.end))
        merged: list[SourceAnchor] = []
        for anchor in ordered:
            if merged and anchor.start <= merged[-1].end:
                current = merged[-1]
                end = max(current.end, anchor.end)
                merged[-1] = (
                    SourceAnchor.text(current.start, end)
                    if current.kind == "text"
                    else SourceAnchor.pdf(current.start, end)
                )
            else:
                merged.append(anchor)
        return tuple(merged)

    def resolve_anchor(self, anchor: SourceAnchor) -> str | bytes:
        self._validate_anchor(anchor)
        if anchor.kind == "text":
            assert self.source_text is not None
            assert anchor.start_offset is not None and anchor.end_offset is not None
            return self.source_text[anchor.start_offset : anchor.end_offset]

        assert self.source_pdf is not None
        assert anchor.start_page is not None and anchor.end_page is not None
        reader = PdfReader(BytesIO(self.source_pdf), strict=True)
        writer = PdfWriter()
        for page_number in range(anchor.start_page - 1, anchor.end_page):
            writer.add_page(reader.pages[page_number])
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def resolve_scene(self, plan: PlannedScene, story_events: Sequence[StoryEvent]) -> SceneContext:
        units_by_id = {unit.unit_id: unit for unit in self.source_units}
        events_by_id: dict[str, StoryEvent] = {}
        all_units = tuple(self.source_units)

        for event in story_events:
            if not event.event_id or event.event_id in events_by_id:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            self._validate_anchor(event.source_anchor)
            if not any(self._contains(unit.source_anchor, event.source_anchor) for unit in all_units):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            events_by_id[event.event_id] = event

        if len(set(plan.source_unit_ids)) != len(plan.source_unit_ids):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        try:
            selected_units = tuple(units_by_id[unit_id] for unit_id in plan.source_unit_ids)
        except KeyError as exc:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED") from exc

        referenced_event_ids = (*plan.retained_event_ids, *plan.omitted_event_ids)
        if len(set(referenced_event_ids)) != len(referenced_event_ids) or any(
            event_id not in events_by_id for event_id in referenced_event_ids
        ):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        retained_events = tuple(events_by_id[event_id] for event_id in plan.retained_event_ids)
        if any(
            not any(self._contains(unit.source_anchor, event.source_anchor) for unit in selected_units)
            for event in retained_events
        ):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        materials = tuple(
            ResolvedSourceMaterial(anchor, self.resolve_anchor(anchor))
            for anchor in self._merge_anchors([unit.source_anchor for unit in selected_units])
        )
        return SceneContext(
            scene_number=plan.scene_number,
            source_unit_ids=tuple(plan.source_unit_ids),
            retained_events=retained_events,
            source_material=materials,
        )


@dataclass(frozen=True)
class AdaptationResult:
    """Transient result retained only while the current Import is being processed."""

    fountain: str
    source_context: SourceContext
    story_plan: StoryPlan | None = field(default=None, repr=False)
    story_events: tuple[StoryEvent, ...] = field(default=(), repr=False)
    character_knowledge: tuple[CharacterKnowledge, ...] = field(default=(), repr=False)
    planned_scenes: tuple[PlannedScene, ...] = ()
    scene_contexts: dict[int, SceneContext] = field(default_factory=dict, repr=False)
    scene_fountains: dict[int, str] = field(default_factory=dict)
    revision_count: dict[int, int] = field(default_factory=dict)
    source_language: SourceLanguage = "und"

    @property
    def source_character_cards(self) -> tuple[SourceCharacterCard, ...]:
        """場面固有の状態を除き、原作にある恒常的な人物情報だけを返す。"""
        return tuple(
            SourceCharacterCard(
                character_name=card.character_name,
                defining_traits=tuple(card.defining_traits),
            )
            for card in self.character_knowledge
        )


def _build_character_knowledge(events: Sequence[StoryEvent]) -> list[CharacterKnowledge]:
    evidence_event_ids: defaultdict[str, list[str]] = defaultdict(list)
    defining_traits: defaultdict[str, list[str]] = defaultdict(list)
    state_by_event: defaultdict[str, dict[str, str]] = defaultdict(dict)
    knowledge_by_event: defaultdict[str, dict[str, str]] = defaultdict(dict)
    relationship_by_event: defaultdict[str, dict[str, str]] = defaultdict(dict)

    for event in events:
        character_names = (
            set(event.character_traits)
            | set(event.character_states)
            | set(event.knowledge_changes)
            | set(event.relationship_changes)
        )
        for character_name in character_names:
            if event.event_id not in evidence_event_ids[character_name]:
                evidence_event_ids[character_name].append(event.event_id)
        for character_name, value in event.character_traits.items():
            if value and value not in defining_traits[character_name]:
                defining_traits[character_name].append(value)
        for character_name, value in event.character_states.items():
            if value:
                state_by_event[character_name][event.event_id] = value
        for character_name, value in event.knowledge_changes.items():
            if value:
                knowledge_by_event[character_name][event.event_id] = value
        for character_name, value in event.relationship_changes.items():
            if value:
                relationship_by_event[character_name][event.event_id] = value

    return [
        CharacterKnowledge(
            character_name=character_name,
            evidence_event_ids=evidence_event_ids[character_name],
            defining_traits=defining_traits[character_name],
            state_by_event=state_by_event[character_name],
            knowledge_by_event=knowledge_by_event[character_name],
            relationship_by_event=relationship_by_event[character_name],
        )
        for character_name in sorted(evidence_event_ids)
    ]


def _validate_narrative_plan(
    plan: NarrativePlan,
    source_context: SourceContext,
    events: Sequence[StoryEvent],
    source_character_knowledge: Sequence[CharacterKnowledge],
) -> None:
    event_map = {event.event_id: event for event in events}
    unit_ids = {unit.unit_id for unit in source_context.source_units}
    scene_numbers = [scene.scene_number for scene in plan.planned_scenes]
    if not plan.planned_scenes or scene_numbers != list(range(1, len(scene_numbers) + 1)):
        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    source_character_names = {item.character_name for item in source_character_knowledge}
    planned_character_names = {item.character_name for item in plan.character_knowledge}
    if planned_character_names != source_character_names:
        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    for knowledge in plan.character_knowledge:
        if not set(knowledge.evidence_event_ids) <= event_map.keys():
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        if not set(knowledge.state_by_event) <= set(knowledge.evidence_event_ids):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        if not set(knowledge.knowledge_by_event) <= set(knowledge.evidence_event_ids):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        if not set(knowledge.relationship_by_event) <= set(knowledge.evidence_event_ids):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    story_plan = plan.story_plan
    all_event_ids = set(event_map)
    if (
        set(story_plan.important_event_ids) - all_event_ids
        or set(story_plan.presentation_order_event_ids) != all_event_ids
        or story_plan.presentation_order_event_ids != [event.event_id for event in events]
        or set(story_plan.character_motivations) - source_character_names
    ):
        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    for turning_point in story_plan.turning_points:
        if not set(turning_point.event_ids) <= all_event_ids:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        expected_evidence = [event_map[event_id].source_anchor for event_id in turning_point.event_ids]
        if turning_point.evidence != expected_evidence:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    for foreshadowing in story_plan.foreshadowings:
        if foreshadowing.setup_event_id not in all_event_ids or (
            foreshadowing.payoff_event_id is not None and foreshadowing.payoff_event_id not in all_event_ids
        ):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    retained_event_ids: set[str] = set()
    omitted_event_ids: set[str] = set()
    omitted_event_counts: dict[str, int] = defaultdict(int)
    for scene in plan.planned_scenes:
        if (
            len(set(scene.source_unit_ids)) != len(scene.source_unit_ids)
            or not set(scene.source_unit_ids) <= unit_ids
            or len(set(scene.retained_event_ids)) != len(scene.retained_event_ids)
            or len(set(scene.omitted_event_ids)) != len(scene.omitted_event_ids)
            or not set(scene.retained_event_ids + scene.omitted_event_ids) <= all_event_ids
            or not set(scene.characters) <= source_character_names
            or any(dependency >= scene.scene_number or dependency < 1 for dependency in scene.dependency_scene_numbers)
            or len(set(scene.dependency_scene_numbers)) != len(scene.dependency_scene_numbers)
        ):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        retained_event_ids.update(scene.retained_event_ids)
        for event_id in scene.omitted_event_ids:
            omitted_event_ids.add(event_id)
            omitted_event_counts[event_id] += 1

    if (
        retained_event_ids & omitted_event_ids
        or any(count != 1 for count in omitted_event_counts.values())
        or not all_event_ids <= retained_event_ids | omitted_event_ids
        or not set(story_plan.important_event_ids) <= retained_event_ids
    ):
        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")


class NarrativeAdaptationConfig(BaseModel):
    """Runtime configuration for the NarrativeAdaptationService."""

    model_config = ConfigDict(extra="forbid")
    text_reader_source_unit_max_characters: int = Field(ge=1)
    text_reader_source_unit_overlap_characters: int = Field(ge=0)
    pdf_classifier_page_window: int = Field(ge=1)
    pdf_reader_page_window: int = Field(ge=1)
    pdf_reader_page_overlap: int = Field(ge=0)
    reader_refinement_max_attempts: int = Field(ge=1)
    scene_regeneration_max_attempts: int = Field(ge=1)
    classification_max_output_tokens: int = Field(ge=1)
    reader_max_output_tokens: int = Field(ge=1)
    planning_max_output_tokens: int = Field(ge=1)
    scene_writing_max_output_tokens: int = Field(ge=1)
    verification_max_output_tokens: int = Field(ge=1)

    @property
    def scene_regeneration_max_retries(self) -> int:
        """Convert the total attempt limit into the number of additional revisions."""
        return self.scene_regeneration_max_attempts - 1


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
    """Merge reader windows while retaining distinct same-summary events with different evidence."""
    all_windows = [result.window for result in results]
    retained_events_by_window: list[tuple[PdfPageWindow, ReaderCandidateEvent]] = []
    retained_units_by_window: list[tuple[PdfPageWindow, SourceUnit]] = []

    for result in results:
        window = result.window
        for candidate_unit in result.candidate_source_units:
            anchor = candidate_unit.source_anchor
            if anchor.start_page is None or anchor.end_page is None:
                continue
            first_containing = next(
                (
                    candidate_window
                    for candidate_window in all_windows
                    if candidate_window.start_page <= anchor.start_page and anchor.end_page <= candidate_window.end_page
                ),
                None,
            )
            if first_containing == window:
                retained_units_by_window.append((window, candidate_unit))

        for candidate_event in result.candidate_events:
            anchor = candidate_event.source_anchor
            if (
                anchor.start_page is not None
                and anchor.end_page is not None
                and window.start_page <= anchor.start_page
                and anchor.end_page <= window.end_page
            ):
                # Keep all in-window candidates. Exact duplicate events from overlap windows are
                # removed below, after comparing their source-grounded character evidence too.
                retained_events_by_window.append((window, candidate_event))

    event_sort_tuples: list[tuple[int, int, int, str, str, ReaderCandidateEvent]] = []
    for window, candidate in retained_events_by_window:
        anchor = candidate.source_anchor
        semantic_fingerprint = repr(
            (
                tuple(sorted(candidate.character_traits.items())),
                tuple(sorted(candidate.character_states.items())),
                tuple(sorted(candidate.knowledge_changes.items())),
                tuple(sorted(candidate.relationship_changes.items())),
                candidate.location_time,
                tuple(candidate.foreshadowing_candidates),
            )
        )
        event_sort_tuples.append(
            (
                window.start_page,
                anchor.start,
                anchor.end,
                normalize_event_summary_key(candidate.summary),
                semantic_fingerprint,
                candidate,
            )
        )
    event_sort_tuples.sort(key=lambda item: item[:5])

    seen_event_keys: set[tuple[int, int, str, str]] = set()
    deduped_events: list[StoryEvent] = []
    for _window_start, anchor_start, anchor_end, summary_key, fingerprint, candidate in event_sort_tuples:
        dedup_key = (anchor_start, anchor_end, summary_key, fingerprint)
        if dedup_key in seen_event_keys:
            continue
        seen_event_keys.add(dedup_key)
        deduped_events.append(
            StoryEvent(
                event_id=f"ev_{len(deduped_events) + 1:04d}",
                summary=candidate.summary,
                source_anchor=candidate.source_anchor,
                character_states=candidate.character_states,
                character_traits=candidate.character_traits,
                knowledge_changes=candidate.knowledge_changes,
                relationship_changes=candidate.relationship_changes,
                location_time=candidate.location_time,
                foreshadowing_candidates=candidate.foreshadowing_candidates,
            )
        )

    seen_unit_anchors: set[tuple[int, int]] = set()
    deduped_units: list[SourceUnit] = []
    for _window, unit in retained_units_by_window:
        unit_key = (unit.source_anchor.start, unit.source_anchor.end)
        if unit_key in seen_unit_anchors:
            continue
        seen_unit_anchors.add(unit_key)
        deduped_units.append(
            SourceUnit(
                unit_id=f"su_{len(deduped_units) + 1:04d}",
                source_anchor=unit.source_anchor,
            )
        )

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
    ) -> list[ReaderCandidateEvent] | None:
        """None は非本文と明示判定した窓。理由のない空一覧とは区別する。"""
        ...

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
        source_context: SourceContext,
        character_knowledge: Sequence[CharacterKnowledge],
        *,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> NarrativePlan: ...

    def write_scene(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
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
        scene_context: SceneContext,
        story_plan: StoryPlan,
        analysis_context: dict[str, object] | None = None,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
    ) -> list[QualityIssue]: ...

    def revise_scene(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
        scene_text: str,
        issues: Sequence[QualityIssue],
        story_plan: StoryPlan,
        *,
        analysis_context: dict[str, object] | None = None,
        on_progress: Callable[[int], None] | None = None,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> str: ...

    def repair_combined_speaker_cues(
        self,
        scene_plan: PlannedScene,
        scene_context: SceneContext,
        scene_text: str,
        *,
        deadline: float | None = None,
        source_language: SourceLanguage = "und",
    ) -> str: ...

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
    ) -> AdaptationResult:
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
            logger.warning("Text reader validation failed reason=no_events_in_document window_count=%d", len(windows))
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        events_by_key: dict[tuple[int, int, str, str], StoryEvent] = {}
        for candidate in raw_events:
            evidence_fingerprint = repr(
                (
                    tuple(sorted(candidate.character_traits.items())),
                    tuple(sorted(candidate.character_states.items())),
                    tuple(sorted(candidate.knowledge_changes.items())),
                    tuple(sorted(candidate.relationship_changes.items())),
                    candidate.location_time,
                    tuple(candidate.foreshadowing_candidates),
                )
            )
            key = (
                candidate.source_anchor.start,
                candidate.source_anchor.end,
                normalize_event_summary_key(candidate.summary),
                evidence_fingerprint,
            )
            if key not in events_by_key:
                event_id = f"ev_{len(events_by_key) + 1:04d}"
                events_by_key[key] = StoryEvent(
                    event_id=event_id,
                    summary=candidate.summary,
                    source_anchor=candidate.source_anchor,
                    character_states=candidate.character_states,
                    character_traits=candidate.character_traits,
                    knowledge_changes=candidate.knowledge_changes,
                    relationship_changes=candidate.relationship_changes,
                    location_time=candidate.location_time,
                    foreshadowing_candidates=candidate.foreshadowing_candidates,
                )
        story_events = list(events_by_key.values())
        source_context = SourceContext.from_text(source_text, source_units)
        return self._run_pipeline(
            story_events,
            source_context,
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
    ) -> AdaptationResult:
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

        expected_windows = {(window.start_page, window.end_page) for window in windows}
        for result in reader_results:
            window_key = (result.window.start_page, result.window.end_page)
            if window_key not in expected_windows:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            for candidate in (*result.candidate_events, *result.candidate_source_units):
                anchor = candidate.source_anchor
                if (
                    anchor.kind != "pdf"
                    or anchor.start_page is None
                    or anchor.end_page is None
                    or anchor.start_page < result.window.start_page
                    or anchor.end_page > result.window.end_page
                    or anchor.end_page < anchor.start_page
                ):
                    raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        merged = merge_pdf_reader_candidates(reader_results)
        if not merged.events:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        source_context = SourceContext.from_pdf(source_pdf, merged.source_units)
        return self._run_pipeline(
            merged.events,
            source_context,
            on_progress,
            ensure_not_cancelled,
            deadline,
        )

    def verify_after_analysis(
        self,
        adaptation: AdaptationResult,
        analysis_context: dict[str, object],
        *,
        on_progress: Callable[[str, int], None] | None = None,
        ensure_not_cancelled: Callable[[], None] | None = None,
        deadline: float | None = None,
        allow_revision: bool = True,
    ) -> AdaptationResult:
        """初回分析を含む根拠付き照合を行い、必要な場面だけを上限内で改稿する。"""
        planned_scenes = sorted(adaptation.planned_scenes, key=lambda item: item.scene_number)
        if not planned_scenes:
            return adaptation
        if adaptation.story_plan is None or set(adaptation.scene_fountains) != {
            plan.scene_number for plan in planned_scenes
        }:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

        scene_fountains = dict(adaptation.scene_fountains)
        scene_contexts = dict(adaptation.scene_contexts)
        revision_count: defaultdict[int, int] = defaultdict(int, adaptation.revision_count)
        plans_by_number = {plan.scene_number: plan for plan in planned_scenes}
        dependency_graph: defaultdict[int, list[int]] = defaultdict(list)
        for plan in planned_scenes:
            for dependency in plan.dependency_scene_numbers:
                dependency_graph[dependency].append(plan.scene_number)

        def related_scene_numbers(plan: PlannedScene) -> list[int]:
            previous = [item for item in planned_scenes if item.scene_number < plan.scene_number]
            pending = list(plan.dependency_scene_numbers)
            if previous:
                pending.append(previous[-1].scene_number)
            related: set[int] = set()
            while pending:
                scene_number = pending.pop()
                if scene_number in related:
                    continue
                related.add(scene_number)
                source_plan = plans_by_number.get(scene_number)
                if source_plan is not None:
                    pending.extend(source_plan.dependency_scene_numbers)
            return sorted(related)

        def scene_context_for(plan: PlannedScene) -> SceneContext:
            context = replace(
                scene_contexts[plan.scene_number],
                related_previous_scenes=tuple(
                    (number, scene_fountains[number])
                    for number in related_scene_numbers(plan)
                    if number in scene_fountains
                ),
            )
            scene_contexts[plan.scene_number] = context
            return context

        def validate_scene_piece(piece: str, plan: PlannedScene) -> None:
            if combined_speaker_passages(piece, plan.characters):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            validate_generated_fountain_cues(
                piece,
                known_source_speakers=plan.characters,
                strict_speaker_names=True,
            )
            parsed = FountainParser.parse(piece)
            heading_valid, planned_number, clean_heading = FountainParser.parse_scene_heading(plan.heading)
            if (
                not heading_valid
                or planned_number != plan.scene_number
                or parsed.scene_count() != 1
                or parsed.scenes[0].scene_number != plan.scene_number
                or parsed.scenes[0].heading != clean_heading
            ):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            utterance_text = "\n".join(item.text for item in parsed.all_utterances())
            validate_text_language(utterance_text, adaptation.source_language)

        def validate_issues(
            plan: PlannedScene,
            context: SceneContext,
            issues: list[QualityIssue],
        ) -> list[QualityIssue]:
            allowed_event_ids = set(plan.retained_event_ids) | set(plan.omitted_event_ids)
            available_anchors = {
                event.source_anchor.model_dump_json()
                for event in adaptation.story_events
                if event.event_id in allowed_event_ids
            }
            available_anchors.update(material.source_anchor.model_dump_json() for material in context.source_material)
            for turning_point in adaptation.story_plan.turning_points:
                if set(turning_point.event_ids) & allowed_event_ids:
                    available_anchors.update(anchor.model_dump_json() for anchor in turning_point.evidence)
            for issue in issues:
                if (
                    issue.scene_number != plan.scene_number
                    or not set(issue.event_ids) <= allowed_event_ids
                    or not {
                        anchor.model_dump_json() for anchor in issue.source_anchors
                    } <= available_anchors
                ):
                    raise NarrativeAdaptationError(
                        "ADAPTATION_VERIFICATION_FAILED",
                        context={"reason": "invalid_verification_reference", "scene_number": plan.scene_number},
                    )
            return issues

        def verify_scene(plan: PlannedScene, text: str) -> list[QualityIssue]:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            context = scene_context_for(plan)
            issues = self.client.verify_scene(
                plan,
                text,
                scene_context=context,
                story_plan=adaptation.story_plan,
                analysis_context=analysis_context,
                deadline=deadline,
            )
            return validate_issues(plan, context, issues)

        def log_verification_decision(
            level: int, reason: str, scene_number: int, issues: Sequence[QualityIssue],
        ) -> None:
            # Log the stop reason and remaining budget without source text, free-form issues, or reference IDs.
            logger.log(
                level,
                "Adaptation verification decision reason=%s scene_number=%d issue_types=%s "
                "major_issue_count=%d minor_issue_count=%d attempt_count=%d max_attempts=%d revision_count=%d",
                reason,
                scene_number,
                ",".join(sorted({issue.issue_type for issue in issues})),
                sum(issue.severity == "major" for issue in issues),
                sum(issue.severity == "minor" for issue in issues),
                revision_count[scene_number] + 1,
                self.config.scene_regeneration_max_attempts,
                revision_count[scene_number],
            )

        initial_issues: dict[int, list[QualityIssue]] = {}
        for index, plan in enumerate(planned_scenes, start=1):
            issues = verify_scene(plan, scene_fountains[plan.scene_number])
            if issues:
                initial_issues[plan.scene_number] = issues
            if on_progress:
                on_progress("verifying", int(index * 100 / len(planned_scenes)))

        if not allow_revision:
            for scene_number, issues in initial_issues.items():
                if any(issue.severity == "major" for issue in issues):
                    log_verification_decision(
                        logging.WARNING, "major_issues_in_final_verification", scene_number, issues,
                    )
                    raise NarrativeAdaptationError.scene_failure(
                        "major_issues_in_final_verification", scene_number, revision_count[scene_number],
                        self.config.scene_regeneration_max_retries, issues,
                    )
            return adaptation

        def reserve_revision(scene_number: int, issues: Sequence[QualityIssue]) -> None:
            if revision_count[scene_number] >= self.config.scene_regeneration_max_retries:
                log_verification_decision(
                    logging.WARNING, "revision_budget_exhausted", scene_number, issues,
                )
                raise NarrativeAdaptationError.scene_failure(
                    "revision_budget_exhausted", scene_number, revision_count[scene_number],
                    self.config.scene_regeneration_max_retries, issues,
                )
            revision_count[scene_number] += 1
            log_verification_decision(logging.INFO, "revising_scene", scene_number, issues)

        def revise_scene_with_budget(
            plan: PlannedScene,
            initial_text: str,
            issues: Sequence[QualityIssue],
        ) -> str:
            current_text = initial_text
            pending_issues = list(issues)
            while pending_issues:
                if ensure_not_cancelled:
                    ensure_not_cancelled()
                reserve_revision(plan.scene_number, pending_issues)
                context = scene_context_for(plan)
                revised_text = self.client.revise_scene(
                    plan,
                    context,
                    current_text,
                    pending_issues,
                    adaptation.story_plan,
                    analysis_context=analysis_context,
                    deadline=deadline,
                    source_language=adaptation.source_language,
                )
                try:
                    validate_scene_piece(revised_text, plan)
                except (GeneratedFountainCueError, NarrativeAdaptationError, ContentLanguageMismatch) as exc:
                    if revision_count[plan.scene_number] >= self.config.scene_regeneration_max_retries:
                        if isinstance(exc, NarrativeAdaptationError):
                            raise NarrativeAdaptationError.scene_failure(
                                "scene_structure_invalid", plan.scene_number, revision_count[plan.scene_number],
                                self.config.scene_regeneration_max_retries,
                            ) from exc
                        raise
                    event_ids = plan.retained_event_ids[:1]
                    source_anchors = [item.source_anchor for item in context.source_material[:1]]
                    if not event_ids and not source_anchors:
                        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED") from exc
                    current_text = revised_text
                    pending_issues = [
                        QualityIssue(
                            scene_number=plan.scene_number,
                            issue_type="fountain_invalid",
                            severity="major",
                            event_ids=event_ids,
                            source_anchors=source_anchors,
                            preservation_condition=(
                                "Maintain the planned scene heading and number, original language, "
                                "speaker identity, and non-empty dialogue."
                            ),
                            description="Scene structure or output language is invalid.",
                            revision_direction=(
                                "Repair the invalid scene heading, speaker cue, dialogue, or output language. "
                                "Preserve all valid source-grounded content."
                            ),
                        )
                    ]
                    continue

                remaining = verify_scene(plan, revised_text)
                if remaining and revision_count[plan.scene_number] < self.config.scene_regeneration_max_retries:
                    current_text = revised_text
                    pending_issues = remaining
                    continue
                if any(issue.severity == "major" for issue in remaining):
                    log_verification_decision(
                        logging.WARNING, "major_issues_after_revision", plan.scene_number, remaining,
                    )
                    raise NarrativeAdaptationError.scene_failure(
                        "major_issues_after_revision", plan.scene_number, revision_count[plan.scene_number],
                        self.config.scene_regeneration_max_retries, remaining,
                    )
                return revised_text
            return current_text

        def downstream_scenes(scene_number: int) -> list[int]:
            visited: set[int] = set()
            pending = deque([scene_number])
            result: list[int] = []
            while pending:
                current = pending.popleft()
                for dependent in dependency_graph[current]:
                    if dependent not in visited:
                        visited.add(dependent)
                        result.append(dependent)
                        pending.append(dependent)
            return sorted(result)

        verification_queue: deque[int] = deque(initial_issues)
        queued_scenes = set(verification_queue)
        reverify_scenes: set[int] = set()
        while verification_queue:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            scene_number = verification_queue.popleft()
            queued_scenes.discard(scene_number)
            plan = plans_by_number[scene_number]
            current_text = scene_fountains[scene_number]
            if scene_number in reverify_scenes:
                reverify_scenes.discard(scene_number)
                issues = verify_scene(plan, current_text)
            else:
                issues = initial_issues.pop(scene_number, [])
            if not issues:
                continue
            if (
                revision_count[scene_number] >= self.config.scene_regeneration_max_retries
                and not any(issue.severity == "major" for issue in issues)
            ):
                log_verification_decision(
                    logging.INFO, "minor_issues_without_revision_budget", scene_number, issues,
                )
                continue

            revised_text = revise_scene_with_budget(plan, current_text, issues)
            scene_fountains[scene_number] = revised_text
            for dependent in downstream_scenes(scene_number):
                reverify_scenes.add(dependent)
                if dependent not in queued_scenes:
                    verification_queue.append(dependent)
                    queued_scenes.add(dependent)

        fountain = "\n".join(scene_fountains[plan.scene_number].strip() for plan in planned_scenes) + "\n"
        return replace(
            adaptation,
            fountain=fountain,
            scene_contexts=scene_contexts,
            scene_fountains=scene_fountains,
            revision_count=dict(revision_count),
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
        for attempt in range(1, self.config.reader_refinement_max_attempts + 1):
            if ensure_not_cancelled:
                ensure_not_cancelled()
            events = self.client.read_text_window(
                text_window,
                source_anchor,
                deadline=deadline,
                source_language=source_language,
            )
            if events is None:
                logger.info(
                    "Text reader skipped non-story window start=%s end=%s",
                    source_anchor.start_offset,
                    source_anchor.end_offset,
                )
                return []
            valid = True
            for ev in events:
                anchor = ev.source_anchor
                if (
                    not ev.summary.strip()
                    or anchor.kind != "text"
                    or anchor.start_offset is None
                    or anchor.end_offset is None
                    or source_anchor.start_offset is None
                    or source_anchor.end_offset is None
                    or anchor.start_offset < source_anchor.start_offset
                    or anchor.end_offset > source_anchor.end_offset
                    or anchor.end_offset <= anchor.start_offset
                ):
                    valid = False
                    break
            if valid and events:
                return events
            logger.warning(
                "Text reader validation failed start=%s end=%s attempt=%d max_attempts=%d reason=%s event_count=%d",
                source_anchor.start_offset,
                source_anchor.end_offset,
                attempt,
                self.config.reader_refinement_max_attempts,
                "empty_events_without_non_story_confirmation" if not events else "invalid_event_anchor_or_summary",
                len(events),
            )
            if attempt >= self.config.reader_refinement_max_attempts:
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        return []

    def _run_pipeline(
        self,
        story_events: list[StoryEvent],
        source_context: SourceContext,
        on_progress: Callable[[str, int], None] | None,
        ensure_not_cancelled: Callable[[], None] | None,
        deadline: float | None,
        *,
        source_language: SourceLanguage = "und",
    ) -> AdaptationResult:
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

        source_character_knowledge = _build_character_knowledge(story_events)
        narrative_plan = self.client.plan_scenes(
            dag,
            source_context,
            source_character_knowledge,
            deadline=deadline,
            source_language=source_language,
        )
        _validate_narrative_plan(narrative_plan, source_context, story_events, source_character_knowledge)
        planned_scenes = sorted(narrative_plan.planned_scenes, key=lambda scene: scene.scene_number)
        scene_by_event: dict[str, int] = {}
        visible_event_ids: set[str] = set()
        scene_contexts: dict[int, SceneContext] = {}
        for plan in planned_scenes:
            visible_event_ids.update(plan.retained_event_ids)
            for event_id in plan.retained_event_ids:
                scene_by_event.setdefault(event_id, plan.scene_number)

            selected_knowledge: list[CharacterKnowledge] = []
            for knowledge in narrative_plan.character_knowledge:
                if knowledge.character_name not in plan.characters:
                    continue
                available_event_ids = set(knowledge.evidence_event_ids) & visible_event_ids
                if not available_event_ids:
                    continue
                selected_knowledge.append(
                    knowledge.model_copy(
                        update={
                            "evidence_event_ids": [
                                event_id for event_id in knowledge.evidence_event_ids if event_id in available_event_ids
                            ],
                            "state_by_event": {
                                event_id: value
                                for event_id, value in knowledge.state_by_event.items()
                                if event_id in available_event_ids
                            },
                            "knowledge_by_event": {
                                event_id: value
                                for event_id, value in knowledge.knowledge_by_event.items()
                                if event_id in available_event_ids
                            },
                            "relationship_by_event": {
                                event_id: value
                                for event_id, value in knowledge.relationship_by_event.items()
                                if event_id in available_event_ids
                            },
                        }
                    )
                )

            unresolved_foreshadowings = tuple(
                item
                for item in narrative_plan.story_plan.foreshadowings
                if (setup_scene := scene_by_event.get(item.setup_event_id)) is not None
                and setup_scene <= plan.scene_number
                and (
                    item.payoff_event_id is None
                    or (payoff_scene := scene_by_event.get(item.payoff_event_id)) is None
                    or payoff_scene > plan.scene_number
                )
            )
            base_context = source_context.resolve_scene(plan, story_events)
            scene_contexts[plan.scene_number] = replace(
                base_context,
                character_knowledge=tuple(selected_knowledge),
                unresolved_foreshadowings=unresolved_foreshadowings,
            )

        # 4. Writing
        if ensure_not_cancelled:
            ensure_not_cancelled()
        if on_progress:
            on_progress("writing", 0)

        def _validate_scene_piece(piece: str, plan: PlannedScene) -> None:
            validate_generated_fountain_cues(
                piece,
                known_source_speakers=plan.characters,
                strict_speaker_names=True,
            )
            parsed = FountainParser.parse(piece)
            heading_valid, planned_number, clean_heading = FountainParser.parse_scene_heading(plan.heading)
            if (
                not heading_valid
                or planned_number != plan.scene_number
                or parsed.scene_count() != 1
                or parsed.scenes[0].scene_number != plan.scene_number
                or parsed.scenes[0].heading != clean_heading
            ):
                raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
            utterance_text = "\n".join(item.text for item in parsed.all_utterances())
            validate_text_language(utterance_text, source_language)

        regeneration_attempts: defaultdict[int, int] = defaultdict(int)
        scene_plan_by_number = {plan.scene_number: plan for plan in planned_scenes}
        scene_fountains: dict[int, str] = {}

        def _reserve_regeneration(scene_number: int) -> None:
            if regeneration_attempts[scene_number] >= self.config.scene_regeneration_max_retries:
                raise NarrativeAdaptationError.scene_failure(
                    "revision_budget_exhausted", scene_number, regeneration_attempts[scene_number],
                    self.config.scene_regeneration_max_retries,
                )
            regeneration_attempts[scene_number] += 1

        def _related_scene_numbers(plan: PlannedScene) -> list[int]:
            prior_plans = [item for item in planned_scenes if item.scene_number < plan.scene_number]
            pending = list(plan.dependency_scene_numbers)
            if prior_plans:
                pending.append(prior_plans[-1].scene_number)
            related: set[int] = set()
            while pending:
                scene_number = pending.pop()
                if scene_number in related:
                    continue
                related.add(scene_number)
                prior_plan = scene_plan_by_number.get(scene_number)
                if prior_plan is not None:
                    pending.extend(prior_plan.dependency_scene_numbers)
            return sorted(related)

        def _scene_context_for(plan: PlannedScene) -> SceneContext:
            context = replace(
                scene_contexts[plan.scene_number],
                related_previous_scenes=tuple(
                    (number, scene_fountains[number])
                    for number in _related_scene_numbers(plan)
                    if number in scene_fountains
                ),
            )
            scene_contexts[plan.scene_number] = context
            return context

        def _write_valid_scene(plan: PlannedScene) -> str:
            repair_issues: list[QualityIssue] = []
            piece = ""
            while True:
                if ensure_not_cancelled:
                    ensure_not_cancelled()
                context = _scene_context_for(plan)
                if repair_issues:
                    piece = self.client.revise_scene(
                        plan,
                        context,
                        piece,
                        repair_issues,
                        narrative_plan.story_plan,
                        deadline=deadline,
                        source_language=source_language,
                    )
                else:
                    piece = self.client.write_scene(
                        plan,
                        context,
                        deadline=deadline,
                        source_language=source_language,
                    )
                if combined_speaker_passages(piece, plan.characters):
                    if ensure_not_cancelled:
                        ensure_not_cancelled()
                    _reserve_regeneration(plan.scene_number)
                    piece = self.client.repair_combined_speaker_cues(
                        plan, context, piece, deadline=deadline, source_language=source_language,
                    )
                    if combined_speaker_passages(piece, plan.characters):
                        raise NarrativeAdaptationError.scene_failure(
                            "scene_structure_invalid", plan.scene_number, regeneration_attempts[plan.scene_number],
                            self.config.scene_regeneration_max_retries,
                        )
                try:
                    _validate_scene_piece(piece, plan)
                    return piece
                except GeneratedFountainCueError as exc:
                    exhausted = regeneration_attempts[plan.scene_number] >= self.config.scene_regeneration_max_retries
                    logger.warning(
                        "Generated scene cue invalid; %s scene_number=%d line_number=%s reason=%s "
                        "attempt_count=%d max_attempts=%d",
                        "attempts exhausted" if exhausted else "regenerating",
                        plan.scene_number,
                        exc.line_number,
                        exc.reason,
                        regeneration_attempts[plan.scene_number] + 1,
                        self.config.scene_regeneration_max_attempts,
                    )
                    if exhausted:
                        raise
                    _reserve_regeneration(plan.scene_number)
                    if exc.reason == "missing_speech":
                        event_ids = plan.retained_event_ids[:1]
                        source_anchors = [material.source_anchor for material in context.source_material[:1]]
                        if not event_ids and not source_anchors:
                            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED") from exc
                        repair_issues = [
                            QualityIssue(
                                scene_number=plan.scene_number,
                                issue_type="fountain_invalid",
                                severity="major",
                                event_ids=event_ids,
                                source_anchors=source_anchors,
                                preservation_condition=(
                                    "Preserve source events, dialogue meaning, and character identity."
                                ),
                                description="Generated Fountain contains missing_speech: a tag-only utterance.",
                                revision_direction=(
                                    "Repair only the invalid utterance. Keep all valid dialogue. "
                                    "Narrate nonverbal behavior or attach a vocal tag to an existing spoken turn; "
                                    "every retained cue must have source-grounded spoken text."
                                ),
                            )
                        ]
                except (NarrativeAdaptationError, ContentLanguageMismatch) as exc:
                    exhausted = regeneration_attempts[plan.scene_number] >= self.config.scene_regeneration_max_retries
                    logger.warning(
                        "Generated scene validation failed; %s scene_number=%d validation_error=%s "
                        "attempt_count=%d max_attempts=%d",
                        "attempts exhausted" if exhausted else "regenerating",
                        plan.scene_number,
                        type(exc).__name__,
                        regeneration_attempts[plan.scene_number] + 1,
                        self.config.scene_regeneration_max_attempts,
                    )
                    if exhausted:
                        if isinstance(exc, NarrativeAdaptationError):
                            raise NarrativeAdaptationError.scene_failure(
                                "scene_structure_invalid", plan.scene_number,
                                regeneration_attempts[plan.scene_number], self.config.scene_regeneration_max_retries,
                            ) from exc
                        raise
                    _reserve_regeneration(plan.scene_number)

        for plan in planned_scenes:
            if ensure_not_cancelled:
                ensure_not_cancelled()
            scene_fountains[plan.scene_number] = _write_valid_scene(plan)

        full_fountain = "\n".join(scene_fountains[plan.scene_number].strip() for plan in planned_scenes) + "\n"
        return AdaptationResult(
            fountain=full_fountain,
            source_context=source_context,
            story_plan=narrative_plan.story_plan,
            story_events=tuple(story_events),
            character_knowledge=tuple(narrative_plan.character_knowledge),
            planned_scenes=tuple(planned_scenes),
            scene_contexts=scene_contexts,
            scene_fountains=dict(scene_fountains),
            revision_count={plan.scene_number: regeneration_attempts[plan.scene_number] for plan in planned_scenes},
            source_language=source_language,
        )
