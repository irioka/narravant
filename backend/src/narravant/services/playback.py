"""Playback planning service for audiobook speech synthesis and playback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from narravant.core.fountain import (
    NARRATOR_SPEAKERS,
    FountainParser,
    ParsedScript,
    UtteranceItem,
    normalize_speaker_name,
)


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
    raw_utterances = _playback_utterances(parsed)

    if not raw_utterances:
        raise PlaybackPlanError(
            code="NO_UTTERANCES",
            message="No utterances found in screenplay source",
        )

    # Build speaker -> voice_id lookup map from voice_assignments.
    # Keep an exact-name map plus a normalized fallback so a voice assigned to a
    # shortened display name (e.g. "奥の声") still resolves an utterance whose
    # cue carries the full alias (e.g. "奥の声（山猫たち）"), and vice versa. The
    # fountain parser only strips ASCII parentheses from cues, so analysis and
    # playback can otherwise disagree on full-width aliased names.
    raw_assignments = document_data.get("voice_assignments") or []
    voice_map: dict[str, str] = {}
    normalized_voice_map: dict[str, str] = {}
    for item in raw_assignments:
        speaker = item.get("speaker")
        voice_id = item.get("voice_id")
        if speaker and voice_id:
            voice_map[speaker] = voice_id
            normalized_key = normalize_speaker_name(speaker)
            if normalized_key:
                normalized_voice_map.setdefault(normalized_key, voice_id)

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

        voice_id = voice_map.get(speaker) or normalized_voice_map.get(normalize_speaker_name(speaker))
        if not voice_id and u.target_type == "narrator":
            # 旧版の日本語キーと英語 cue は、同じナレーター役の声として照合する。
            voice_id = next((voice_map[name] for name in NARRATOR_SPEAKERS if name in voice_map), None)
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


def _playback_utterances(parsed: ParsedScript) -> list[UtteranceItem]:
    """Add speakable scene-heading content to the playback-only utterance list."""
    existing_utterances = parsed.all_utterances()
    narrator_speaker = next(
        (item.speaker for item in existing_utterances if item.target_type == "narrator"),
        None,
    )
    if narrator_speaker is None:
        has_japanese = any(
            "\u3040" <= character <= "\u30ff" or "\u3400" <= character <= "\u9fff"
            for character in parsed.source_fountain
        )
        narrator_speaker = "ナレーター" if has_japanese else "Narrator"

    result: list[UtteranceItem] = []
    for scene in parsed.scenes:
        spoken_heading = FountainParser.spoken_scene_heading(scene.heading)
        heading_offset = 0
        if spoken_heading:
            result.append(
                UtteranceItem(
                    scene_number=scene.scene_number,
                    utterance_index=0,
                    speaker=narrator_speaker,
                    target_type="narrator",
                    text=spoken_heading,
                )
            )
            heading_offset = 1
        result.extend(
            replace(item, utterance_index=item.utterance_index + heading_offset)
            for item in scene.utterances
        )
    return result
