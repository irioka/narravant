"""Validation for generated Fountain screenplay cues."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from narravant.core.fountain import (
    NARRATOR_SPEAKERS,
    FountainParser,
    is_parenthetical_line,
    sanitize_fountain_text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class GeneratedFountainCueError(ValueError):
    """Raised when generated Fountain contains invalid speaker cues."""

    def __init__(self, reason: str, *, line_number: int | None = None, scene_number: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line_number = line_number
        self.scene_number = scene_number


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

    known_normalized = {
        _ASCII_EXTENSION_REGEX.sub("", name).strip()
        for name in known_source_speakers
        if name
    }
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
