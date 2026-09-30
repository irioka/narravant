"""Playback planning service for audiobook speech synthesis and playback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from narravant.core.fountain import FountainParser


class PlaybackPlanError(Exception):
    """Exception raised when a playback plan cannot be constructed."""

    def __init__(
        self,
        code: str,
        message: str,
        speaker: str | None = None,
        scene_number: int | None = None,
        utterance_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.speaker = speaker
        self.scene_number = scene_number
        self.utterance_index = utterance_index


@dataclass(frozen=True)
class PlannedUtterance:
    """A single utterance ready for TTS synthesis and playback."""

    scene_number: int
    utterance_index: int
    speaker: str
    target_type: str  # "narrator" or "character"
    text: str
    voice_id: str
    performance_direction: str = ""  # Fountain parenthetical, sent to TTS as guidance


@dataclass(frozen=True)
class PlaybackPlan:
    """An ordered plan of utterances for real-time playback."""

    document_id: str
    version_id: int
    utterances: list[PlannedUtterance]
    total_utterances: int


def build_playback_plan(
    document_data: dict[str, Any],
    start_scene: int = 1,
    start_utterance_index: int = 0,
) -> PlaybackPlan:
    """Build a validated playback plan from document structured data.

    Validates:
    - Screenplay has at least one utterance (R2.6 / SC-2).
    - Every utterance has an identified speaker (R4.5).
    - Every speaker has an assigned, valid voice_id (R4.5, R4.6).

    Filters utterances to begin from (start_scene, start_utterance_index)
    to support resume / retry from point of failure (R6.1, R7.1).
    """
    document_id = document_data.get("document_id", "")
    version_id = document_data.get("version_id", 1)
    source_fountain = document_data.get("source_fountain", "")

    parsed = FountainParser.parse(source_fountain)
    raw_utterances = parsed.all_utterances()

    if not raw_utterances:
        raise PlaybackPlanError(
            code="NO_UTTERANCES",
            message="No utterances found in screenplay source",
        )

    # Build speaker -> voice_id lookup map from voice_assignments
    raw_assignments = document_data.get("voice_assignments") or []
    voice_map: dict[str, str] = {}
    for item in raw_assignments:
        speaker = item.get("speaker")
        voice_id = item.get("voice_id")
        if speaker and voice_id:
            voice_map[speaker] = voice_id

    # Validate all utterances and resolve voices
    all_planned: list[PlannedUtterance] = []
    for u in raw_utterances:
        speaker = (u.speaker or "").strip()
        if not speaker:
            raise PlaybackPlanError(
                code="MISSING_SPEAKER",
                message=f"Missing speaker for utterance in scene {u.scene_number}",
                scene_number=u.scene_number,
                utterance_index=u.utterance_index,
            )

        voice_id = voice_map.get(speaker)
        if not voice_id:
            raise PlaybackPlanError(
                code="UNASSIGNED_VOICE",
                message=f"Speaker '{speaker}' is not assigned a voice in scene {u.scene_number}",
                speaker=speaker,
                scene_number=u.scene_number,
                utterance_index=u.utterance_index,
            )

        all_planned.append(
            PlannedUtterance(
                scene_number=u.scene_number,
                utterance_index=u.utterance_index,
                speaker=speaker,
                target_type=u.target_type,
                text=u.text,
                voice_id=voice_id,
                performance_direction=getattr(u, "performance_direction", "") or "",
            )
        )

    # Filter starting position
    filtered = [
        item
        for item in all_planned
        if (item.scene_number > start_scene)
        or (item.scene_number == start_scene and item.utterance_index >= start_utterance_index)
    ]

    return PlaybackPlan(
        document_id=document_id,
        version_id=version_id,
        utterances=filtered,
        total_utterances=len(all_planned),
    )
