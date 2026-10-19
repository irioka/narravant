"""Validation for generated Fountain screenplay cues."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from narravant.core.fountain import (
    NARRATOR_SPEAKERS,
    FountainParser,
    is_parenthetical_line,
    normalize_speaker_name,
    sanitize_fountain_text,
)
from narravant.core.speech_notation import (
    SpeechNotationError,
    validate_generated_speech_notation,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class GeneratedFountainCueError(ValueError):
    """Raised when generated Fountain contains invalid speaker cues or notation."""

    def __init__(
        self,
        reason: str,
        *,
        line_number: int | None = None,
        scene_number: int | None = None,
        offset: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line_number = line_number
        self.scene_number = scene_number
        self.offset = offset


_SENTENCE_IN_CUE_REGEX = re.compile(
    r"^[A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*)*\s+[a-z].*[.!?]$"
)
_ASCII_EXTENSION_REGEX = re.compile(r"\s*\([^)]*\)$")


def validate_generated_fountain_cues(
    fountain: str,
    *,
    known_source_speakers: Sequence[str] = (),
    strict_speaker_names: bool = False,
) -> None:
    """Validate speaker cues in generated Fountain text.

    Raises:
        GeneratedFountainCueError: If invalid cues are detected.
    """
    sanitized = sanitize_fountain_text(fountain)
    lines = sanitized.splitlines()

    known_normalized = {normalize_speaker_name(name) for name in known_source_speakers if name}
    known_exact = {name.strip() for name in known_source_speakers if name}

    in_title_page = True
    title_key_found = False
    pending_cue_has_speech = True
    pending_cue_line: int | None = None
    pending_cue_scene: int | None = None
    current_scene: int | None = None

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()

        if in_title_page:
            if not stripped:
                if title_key_found:
                    in_title_page = False
                continue

            is_heading, _, _ = FountainParser.parse_scene_heading(stripped)
            if is_heading or stripped.startswith("#"):
                in_title_page = False
            elif ":" in stripped:
                key, _ = stripped.split(":", 1)
                clean_key = key.strip().lower()
                if clean_key in (
                    "title",
                    "credit",
                    "author",
                    "authors",
                    "source",
                    "notes",
                    "draft date",
                    "date",
                    "contact",
                    "copyright",
                    "logline",
                    "log line",
                    "synopsis",
                    "format",
                    "genre",
                    "genre1",
                    "genre2",
                    "characters",
                    "cast",
                ):
                    title_key_found = True
                    continue
                in_title_page = False
            else:
                in_title_page = False

        if not stripped:
            continue

        is_heading, scene_number, _ = FountainParser.parse_scene_heading(stripped)
        if is_heading or stripped.startswith("#"):
            if not pending_cue_has_speech:
                raise GeneratedFountainCueError(
                    "cue_without_speech", line_number=pending_cue_line, scene_number=pending_cue_scene
                )
            if is_heading:
                current_scene = scene_number
            pending_cue_has_speech = True
            continue

        if stripped.startswith("[["):
            continue

        if stripped.startswith("@"):
            if not pending_cue_has_speech:
                raise GeneratedFountainCueError(
                    "cue_without_speech", line_number=pending_cue_line, scene_number=pending_cue_scene
                )

            if "@" in stripped[1:]:
                raise GeneratedFountainCueError(
                    "inline_at_in_cue", line_number=line_number, scene_number=current_scene
                )

            cue_raw = stripped[1:].strip()
            clean_name = _ASCII_EXTENSION_REGEX.sub("", cue_raw).strip()
            is_narrator = clean_name in NARRATOR_SPEAKERS

            if strict_speaker_names:
                if not is_narrator and clean_name not in known_normalized and cue_raw not in known_exact:
                    raise GeneratedFountainCueError(
                        "unknown_character_cue", line_number=line_number, scene_number=current_scene
                    )

            is_known = clean_name in known_normalized or cue_raw in known_exact
            if not is_known and _SENTENCE_IN_CUE_REGEX.search(clean_name):
                raise GeneratedFountainCueError(
                    "sentence_in_cue", line_number=line_number, scene_number=current_scene
                )

            pending_cue_has_speech = False
            pending_cue_line = line_number
            pending_cue_scene = current_scene
        else:
            if not pending_cue_has_speech and not is_parenthetical_line(stripped):
                pending_cue_has_speech = True

    if not pending_cue_has_speech:
        raise GeneratedFountainCueError(
            "cue_without_speech", line_number=pending_cue_line, scene_number=pending_cue_scene
        )

    try:
        parsed = FountainParser.parse(sanitized)
    except SpeechNotationError as exc:
        raise GeneratedFountainCueError(
            exc.reason,
            line_number=exc.line_number,
            scene_number=exc.scene_number,
            offset=exc.offset,
        ) from exc

    for utterance in parsed.all_utterances():
        try:
            validate_generated_speech_notation(utterance.text, utterance.listener)
        except SpeechNotationError as exc:
            raise GeneratedFountainCueError(
                exc.reason,
                line_number=utterance.source_line,
                scene_number=utterance.scene_number,
                offset=exc.offset,
            ) from exc
        if strict_speaker_names and utterance.listener is not None:
            normalized_listener = normalize_speaker_name(utterance.listener)
            if normalized_listener not in known_normalized and utterance.listener not in known_exact:
                raise GeneratedFountainCueError(
                    "unknown_listener",
                    line_number=utterance.source_line,
                    scene_number=utterance.scene_number,
                )


def combined_speaker_passages(
    fountain: str, known_source_speakers: Sequence[str] = (),
) -> tuple[tuple[int, int], ...]:
    """複合話者の疑いがある生成cueの発話範囲を返す。話者の割当は推測しない。"""
    names = {normalize_speaker_name(name) for name in known_source_speakers if name}
    lines = fountain.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = []
    for index, line in enumerate(lines):
        if not line.strip().startswith("@"):
            continue
        name = _ASCII_EXTENSION_REGEX.sub("", line.strip()[1:]).strip()
        english_pair = bool(re.search(r"\s+and\s+|\s*&\s*", name, re.IGNORECASE))
        japanese_pair = any(
            left and right and (
                left in names or right in names
                or (re.search(r"[一-龯ァ-ヶ]", left) and re.search(r"[一-龯ァ-ヶ]", right))
            )
            for left, separator, right in [name.partition("と")]
            if separator
        )
        if not english_pair and not japanese_pair:
            continue
        end = index + 1
        while end < len(lines):
            candidate = lines[end].strip()
            if not candidate or candidate.startswith("@") or FountainParser.parse_scene_heading(candidate)[0]:
                break
            end += 1
        end_offset = offsets[end]
        while end_offset > offsets[index] and fountain[end_offset - 1] in "\r\n":
            end_offset -= 1
        spans.append((offsets[index], end_offset))
    return tuple(spans)
