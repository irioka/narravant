"""Pydantic v2 schemas for NARRAVANT API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DialogueSchema(BaseModel):
    """Dialogue line within a scene."""

    model_config = ConfigDict(extra="ignore")
    character: str
    line: str


class SceneSchema(BaseModel):
    """Structured scene in a screenplay."""

    model_config = ConfigDict(extra="ignore")
    scene_number: int
    heading: str
    text: str
    dialogues: list[DialogueSchema]


class EmotionArcPointMappingSchema(BaseModel):
    """One aggregated emotion-arc point's inclusive source-scene range."""

    model_config = ConfigDict(extra="forbid")
    point_number: int = Field(ge=1)
    start_scene_number: int = Field(ge=1)
    end_scene_number: int = Field(ge=1)
    representative_scene_number: int = Field(ge=1)


class EmotionArcSchema(BaseModel):
    """Valence/tension data (valence 1-7 int, non-appearance 0, tension -3..+3 int)."""

    model_config = ConfigDict(extra="ignore")
    valence: list[int]
    tension: list[int]
    characters: dict[str, list[int]]
    scene_mapping: list[EmotionArcPointMappingSchema]
    valence_vector: list[float] = Field(min_length=10, max_length=10)


class TurningPointCharacterSchema(BaseModel):
    """One character's goal/conflict/choice/action/change at a turning point."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    conflict: str = Field(min_length=1)
    choice: str = Field(min_length=1)
    action: str = Field(min_length=1)
    change: str = Field(min_length=1)


class IdentifiedTurningPointSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tp_number: int = Field(ge=1, le=5)
    label: str
    availability: Literal["identified"] = "identified"
    scene_number: int = Field(ge=1)
    change: str = Field(min_length=1)
    involved_characters: list[TurningPointCharacterSchema] = Field(min_length=1)
    reason: None = None


class NotApplicableTurningPointSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tp_number: int = Field(ge=1, le=5)
    label: str
    availability: Literal["not_applicable"] = "not_applicable"
    scene_number: None = None
    change: None = None
    involved_characters: list[TurningPointCharacterSchema] = Field(default_factory=list, max_length=0)
    reason: str = Field(min_length=1)


TurningPointSchema = Annotated[
    IdentifiedTurningPointSchema | NotApplicableTurningPointSchema,
    Field(discriminator="availability"),
]


class CharacterAnalysisSchema(BaseModel):
    """Main character analysis stored in the canonical v1 document payload."""

    model_config = ConfigDict(extra="ignore")
    name: str
    external_goal: str | None = None
    internal_need: str | None = None
    fear_or_cost: str | None = None
    obstacle: str | None = None
    choice: str | None = None
    agency: str | None = None
    goal_to_outcome: str | None = None
    related_turning_points: list[int] = Field(default_factory=list)
    voice_traits: str = ""


class NarratorSchema(BaseModel):
    """Voice configuration for the narrator."""

    model_config = ConfigDict(extra="ignore")
    voice_traits: str = ""


class VoiceAssignmentSchema(BaseModel):
    """Assignment of a voice to a speaker (narrator or character)."""

    model_config = ConfigDict(extra="ignore")
    speaker: str = Field(min_length=1)
    voice_id: str | None = None
    voice_traits: str = ""


class AnalysisSchema(BaseModel):
    """Canonical v1 analysis payload returned with each document version."""

    model_config = ConfigDict(extra="ignore")
    status: Literal["completed", "not_requested"]
    turning_points: list[TurningPointSchema]
    characters: list[CharacterAnalysisSchema]


class DocumentItem(BaseModel):
    """Summary item for document list view."""

    model_config = ConfigDict(extra="ignore")
    document_id: str
    owner_user_id: str
    title: str
    current_version_id: int
    version_id: int
    expected_version: int
    is_saved: bool
    created_at: str
    updated_at: str
    owner_email: str
    shared_count: int
    gcs_uri: str | None = None
    arc_distance: float | None = None


class DocumentListResponse(BaseModel):
    """Paginated document list response."""

    items: list[DocumentItem]
    total: int
    limit: int
    offset: int


class DocumentCapabilitiesSchema(BaseModel):
    """Actions authorized for the requesting user on a document."""

    can_edit: bool
    can_share: bool
    can_delete: bool


class CurrentUserResponse(BaseModel):
    """NARRAVANT local identity exposed to the signed-in frontend."""

    user_id: str
    email: str
    display_name: str


class DocumentDetailResponse(BaseModel):
    """Full document detail with scenes and emotion arc from storage."""

    document_id: str
    owner_user_id: str
    title: str
    current_version_id: int
    version_id: int
    expected_version: int
    is_saved: bool
    created_at: str
    updated_at: str
    owner_email: str
    shared_count: int
    gcs_uri: str | None = None
    generation: int | None = None
    source_fountain: str
    capabilities: DocumentCapabilitiesSchema
    metadata: dict[str, Any]
    scenes: list[SceneSchema]
    analysis: AnalysisSchema
    emotion_arc: EmotionArcSchema
    narrator: NarratorSchema = Field(default_factory=NarratorSchema)
    voice_assignments: list[VoiceAssignmentSchema] = Field(default_factory=list)


class DocumentVersionItem(BaseModel):
    version_id: int
    title: str
    created_at: str
    payload_sha256: str
    is_current: bool


class DocumentVersionListResponse(BaseModel):
    document_id: str
    current_version_id: int
    items: list[DocumentVersionItem]


class DocumentUpdateRequest(BaseModel):
    """Request to update document metadata and/or script text with optimistic locking."""

    expected_version: int = Field(description="Expected current version_id for optimistic locking")
    base_version_id: int | None = Field(default=None, ge=1, description="Version currently being edited")
    title: str | None = None
    fountain_text: str | None = None
    metadata: dict[str, Any] | None = None
    analysis: AnalysisSchema | None = None
    emotion_arc: EmotionArcSchema | None = None
    narrator: NarratorSchema | None = None
    voice_assignments: list[VoiceAssignmentSchema] | None = None

    @model_validator(mode="after")
    def requires_title_or_fountain(self) -> DocumentUpdateRequest:
        if (
            self.title is None
            and self.fountain_text is None
            and self.metadata is None
            and self.analysis is None
            and self.emotion_arc is None
            and self.narrator is None
            and self.voice_assignments is None
        ):
            raise ValueError(
                "title、fountain_text、metadata、analysis、emotion_arc、narrator、voice_assignmentsのいずれかを指定してください"
            )
        if self.title is not None and not self.title.strip():
            raise ValueError("titleは空白のみにできません")
        if self.fountain_text is not None and not self.fountain_text.strip():
            raise ValueError("fountain_textは空白のみにできません")
        return self


class TaskAcceptedResponse(BaseModel):
    task_id: str
    task_type: str
    status: str = "queued"


class NativeExchangeModel(BaseModel):
    """Trust-boundary model: external Native JSON has no extension fields."""

    model_config = ConfigDict(extra="forbid")


class NativeMetadataSchema(NativeExchangeModel):
    title: str = Field(min_length=1)
    logline: str = Field(min_length=1)
    synopsis: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    theme_setting: str = Field(min_length=1)


class ImportDraftSaveRequest(BaseModel):
    """Current client-side Import draft to publish in the single explicit Save action."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    fountain_text: str = Field(min_length=1)
    metadata: NativeMetadataSchema
    analysis: AnalysisSchema
    emotion_arc: EmotionArcSchema
    narrator: NarratorSchema = Field(default_factory=NarratorSchema)
    voice_assignments: list[VoiceAssignmentSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_blank_title_or_fountain(self) -> ImportDraftSaveRequest:
        if not self.title.strip():
            raise ValueError("titleは空白のみにできません")
        if not self.fountain_text.strip():
            raise ValueError("fountain_textは空白のみにできません")
        return self


class NativeTurningPointCharacterSchema(NativeExchangeModel):
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    conflict: str = Field(min_length=1)
    choice: str = Field(min_length=1)
    action: str = Field(min_length=1)
    change: str = Field(min_length=1)


class NativeIdentifiedTurningPointSchema(NativeExchangeModel):
    tp_number: int = Field(ge=1, le=5)
    availability: Literal["identified"] = "identified"
    scene_number: int = Field(ge=1)
    change: str = Field(min_length=1)
    involved_characters: list[NativeTurningPointCharacterSchema] = Field(min_length=1)
    reason: None = None


class NativeNotApplicableTurningPointSchema(NativeExchangeModel):
    tp_number: int = Field(ge=1, le=5)
    availability: Literal["not_applicable"] = "not_applicable"
    scene_number: None = None
    change: None = None
    involved_characters: list[NativeTurningPointCharacterSchema] = Field(default_factory=list, max_length=0)
    reason: str = Field(min_length=1)


NativeTurningPointSchema = Annotated[
    NativeIdentifiedTurningPointSchema | NativeNotApplicableTurningPointSchema,
    Field(discriminator="availability"),
]


class NativeCharacterSchema(NativeExchangeModel):
    name: str = Field(min_length=1)
    external_goal: str | None = None
    internal_need: str | None = None
    fear_or_cost: str | None = None
    obstacle: str | None = None
    choice: str | None = None
    agency: str | None = None
    goal_to_outcome: str | None = None
    related_turning_points: list[int] = Field(default_factory=list)
    voice_traits: str = ""


class NativeNarratorSchema(NativeExchangeModel):
    voice_traits: str = ""


class NativeVoiceAssignmentSchema(NativeExchangeModel):
    speaker: str = Field(min_length=1)
    voice_id: str | None = None
    voice_traits: str = ""


class NativeAnalysisSchema(NativeExchangeModel):
    status: Literal["completed"]
    turning_points: list[NativeTurningPointSchema] = Field(min_length=5, max_length=5)
    characters: list[NativeCharacterSchema] = Field(default_factory=list)


class NativeEmotionArcSchema(NativeExchangeModel):
    valence: list[int] = Field(min_length=1)
    tension: list[int] = Field(min_length=1)
    characters: dict[str, list[int]] = Field(default_factory=dict)


class NativeDocumentSchema(NativeExchangeModel):
    title: str = Field(min_length=1)
    source_fountain: str = Field(min_length=1)
    metadata: NativeMetadataSchema
    analysis: NativeAnalysisSchema
    emotion_arc: NativeEmotionArcSchema
    narrator: NativeNarratorSchema = Field(default_factory=NativeNarratorSchema)
    voice_assignments: list[NativeVoiceAssignmentSchema] = Field(default_factory=list)


class NativeExchangeEnvelope(NativeExchangeModel):
    format: Literal["narravant-native"]
    format_version: Literal[1]
    exported_at: datetime
    document: NativeDocumentSchema


class ImportDraftResponse(BaseModel):
    """Ephemeral completed-import payload. It is never written to task_events."""

    document_id: str
    expected_version: int | None = Field(default=None, ge=1)
    version_id: int = Field(ge=1)
    title: str
    source_fountain: str
    metadata: dict[str, Any]
    scenes: list[SceneSchema]
    analysis: AnalysisSchema
    emotion_arc: EmotionArcSchema
    narrator: NarratorSchema = Field(default_factory=NarratorSchema)
    voice_assignments: list[VoiceAssignmentSchema] = Field(default_factory=list)


class ImportSaveResponse(BaseModel):
    document_id: str
    version_id: int
    expected_version: int


class TaskCancelResponse(BaseModel):
    task_id: str
    status: str = "cancel_requested"


TaskProgressPhase = Literal[
    "queued",
    "cancelling",
    "structuring",
    "classifying",
    "reading",
    "building_plot_graph",
    "planning",
    "writing",
    "verifying",
    "analyzing",
]


class TaskProgressEvent(BaseModel):
    phase: TaskProgressPhase
    percentage: int = Field(ge=0, le=100)
    message: str
    received_characters: int | None = Field(default=None, ge=0)


class ReanalysisTaskResultEvent(BaseModel):
    """Validated emotion-arc draft returned by a completed reanalysis task."""

    emotion_arc: EmotionArcSchema


class TaskCompletedEvent(BaseModel):
    document_id: str | None = None
    version_id: int | None = None
    reanalysis: ReanalysisTaskResultEvent | None = None
    import_draft: ImportDraftResponse | None = None


class TaskErrorEvent(BaseModel):
    code: str
    message: str
    retryable: bool


class TaskCancelledEvent(BaseModel):
    message: str


class ValenceSimilarityItem(BaseModel):
    pattern_id: str
    name: str
    description: str
    percentage: float = Field(ge=0, le=100)


class ValencePatternItem(BaseModel):
    """One configured valence-arc pattern safe to expose to the UI."""

    pattern_id: str
    name: str
    description: str


class ValencePatternListResponse(BaseModel):
    """Configured Valence pattern catalogue in its canonical config order."""

    items: list[ValencePatternItem]


class ValenceSimilarityResponse(BaseModel):
    document_id: str
    version_id: int
    items: list[ValenceSimilarityItem]
