"""Fountain screenplay parser for NARRAVANT.

Parses Fountain syntax plain text into structured screenplay documents,
extracting title page metadata, scene headings, scene numbers, action lines,
and character dialogues according to the v1 JSON schema specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

# Pattern to clean unwanted LLM signature artifacts if present
_LLM_SIGNATURE_PATTERN = re.compile(
    r"\[?\{['\"]type['\"]:\s*['\"]text['\"],\s*['\"]text['\"]:\s*['\"]['\"],\s*['\"]extras['\"]:\s*\{['\"]signature['\"]:[^}]+\}[^]]*\]?",
    re.DOTALL,
)

# Standard scene heading prefix pattern
_SCENE_HEADING_REGEX = re.compile(
    r"^(INT|EXT|EST|INT\./EXT|INT/EXT|I/E)[\.\s]",
    re.IGNORECASE,
)

# Scene number in trailing #number# pattern
_SCENE_NUMBER_REGEX = re.compile(r"#(\d+)#\s*$")

# Fountain files from Japanese writers commonly use full-width brackets for
# parentheticals.  Keep the recognition in one place so parsing and generated
# script normalization agree on what is performance guidance rather than
# spoken text.
_PARENTHETICAL_LINE_REGEX = re.compile(r"^(?:\(|（).*(?:\)|）)$")
_ASCII_CHARACTER_CUE_REGEX = re.compile(r"^[A-Z][A-Z0-9 ._'/-]{0,38}$")
NARRATOR_SPEAKERS = ("ナレーター", "Narrator", "NARRATOR")

# A character cue may carry a parenthesized alias/role, e.g. ``茨城暦（宇賀貞治）``
# or ``奥の声（山猫たち）``. Both half-width and full-width brackets occur in
# Japanese scripts. Keep the recognition in one place so analysis, the Fountain
# parser, and playback agree on how to collapse a display name to its stable
# identity when matching speakers to voices.
_SPEAKER_ALIAS_REGEX = re.compile(r"[\(（\[［].*?[\)）\]］]")
_WHITESPACE_REGEX = re.compile(r"\s+")


def normalize_speaker_name(name: str) -> str:
    """Collapse a speaker display name to its alias-free, whitespace-free form.

    Used to reconcile the full cue name carried by utterances (which keeps any
    parenthesized alias, e.g. ``奥の声（山猫たち）``) with a voice assignment
    that may have been keyed on the shortened name (e.g. ``奥の声``). Returns an
    empty string for falsy input.
    """
    if not name:
        return ""
    without_alias = _SPEAKER_ALIAS_REGEX.sub("", name)
    return _WHITESPACE_REGEX.sub("", without_alias).strip()


def is_parenthetical_line(line: str) -> bool:
    """Return whether a line is a Fountain performance-direction line."""
    return bool(_PARENTHETICAL_LINE_REGEX.fullmatch(line.strip()))


def is_ascii_character_cue(line: str) -> bool:
    """Return whether a line is an unforced, ASCII all-caps character cue.

    ``str.isupper()`` returns true for Japanese prose containing one uppercase
    Latin initial because uncased characters are ignored. Restricting the
    fallback cue heuristic to the conventional ASCII Fountain form prevents
    lines such as ``紳士Aは犬を見下ろす。`` from becoming speaker names.
    """
    candidate = re.sub(r"\s*\([^)]*\)$", "", line.strip()).strip()
    return bool(_ASCII_CHARACTER_CUE_REGEX.fullmatch(candidate))


def sanitize_fountain_text(text: str) -> str:
    """Sanitize input text by removing LLM signatures and trailing markers."""
    if not text:
        return ""
    cleaned = _LLM_SIGNATURE_PATTERN.sub("", text)
    cleaned = cleaned.replace("[FOUNTAIN_CONVERSION_COMPLETE]", "")
    return cleaned.strip()


@dataclass
class DialogueItem:
    """Represents a single spoken dialogue line."""

    character: str
    line: str

    def to_dict(self) -> dict[str, str]:
        return {"character": self.character, "line": self.line}


@dataclass
class UtteranceItem:
    """Represents a single spoken or narrated utterance in script order."""

    scene_number: int
    utterance_index: int
    speaker: str
    target_type: Literal["narrator", "character"]
    text: str
    performance_direction: str = ""  # Fountain parenthetical, sent to TTS as performance guidance

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "utterance_index": self.utterance_index,
            "speaker": self.speaker,
            "target_type": self.target_type,
            "text": self.text,
            "performance_direction": self.performance_direction,
        }


@dataclass
class ParsedScene:
    """Structured representation of a screenplay scene."""

    scene_number: int
    heading: str
    text: str = ""
    dialogues: list[DialogueItem] = field(default_factory=list)
    utterances: list[UtteranceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "heading": self.heading,
            "text": self.text.strip(),
            "dialogues": [d.to_dict() for d in self.dialogues],
        }


@dataclass
class ScriptMetadata:
    """Title page and general screenplay metadata."""

    title: str = ""
    logline: str = ""
    synopsis: str = ""
    format: str = ""
    genre1: str = ""
    genre2: str = ""
    characters: list[str] = field(default_factory=list)
    raw_key_values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "logline": self.logline,
            "synopsis": self.synopsis,
            "characters": self.characters,
        }


@dataclass
class ParsedScript:
    """Complete parsed screenplay document."""

    metadata: ScriptMetadata
    scenes: list[ParsedScene] = field(default_factory=list)
    raw_outline: str = ""
    raw_script_body: str = ""
    source_fountain: str = ""

    def scene_count(self) -> int:
        return len(self.scenes)

    def utterance_count(self) -> int:
        return sum(len(s.utterances) for s in self.scenes)

    def all_utterances(self) -> list[UtteranceItem]:
        result: list[UtteranceItem] = []
        for s in self.scenes:
            result.extend(s.utterances)
        return result

    def character_names(self) -> list[str]:
        """Return all declared or spoken character names in stable order."""
        seen: list[str] = []
        for name in self.metadata.characters:
            if name and name not in seen:
                seen.append(name)
        for s in self.scenes:
            for d in s.dialogues:
                if (
                    d.character
                    and d.character not in NARRATOR_SPEAKERS
                    and d.character not in seen
                ):
                    seen.append(d.character)
        return seen

    def dialogue_character_names(self) -> list[str]:
        """Return only non-narrator speakers that have spoken dialogue."""
        seen: list[str] = []
        for utterance in self.all_utterances():
            if utterance.target_type != "character":
                continue
            if utterance.speaker and utterance.speaker not in seen:
                seen.append(utterance.speaker)
        return seen

    def to_v1_json(
        self,
        document_id: str,
        owner_user_id: str,
        source: dict[str, str],
        analysis: dict[str, Any],
        version_id: int = 1,
        emotion_arc: dict[str, Any] | None = None,
        narrator: dict[str, Any] | None = None,
        voice_assignments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Convert to v1 schema JSON structure."""
        return {
            "schema_version": 1,
            "document_id": document_id,
            "version_id": version_id,
            "owner_user_id": owner_user_id,
            "source": source,
            "source_fountain": self.source_fountain,
            "metadata": self.metadata.to_dict(),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "analysis": analysis,
            "emotion_arc": emotion_arc
            or {
                "valence": [],
                "tension": [],
                "characters": {},
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": narrator if narrator is not None else {"voice_traits": ""},
            "voice_assignments": voice_assignments if voice_assignments is not None else [],
        }


class FountainParser:
    """Parser for Fountain-formatted screenplay text."""

    @staticmethod
    def parse_scene_heading(line: str) -> tuple[bool, int | None, str]:
        """Inspect a line to determine if it is a scene heading.

        Returns:
            (is_heading, scene_number_if_specified, clean_heading_text)
        """
        if not line or not line.strip():
            return False, None, ""

        stripped = line.strip()

        # Forced action line starting with ! is not a scene heading
        if stripped.startswith("!"):
            return False, None, ""

        is_forced_heading = stripped.startswith(".") and len(stripped) > 1 and not stripped.startswith("...")
        is_standard_heading = bool(_SCENE_HEADING_REGEX.match(stripped))
        is_numbered_heading = bool(_SCENE_NUMBER_REGEX.search(stripped))

        if not (is_forced_heading or is_standard_heading or is_numbered_heading):
            return False, None, ""

        # Extract explicit scene number if #n# is present
        scene_num: int | None = None
        match = _SCENE_NUMBER_REGEX.search(stripped)
        if match:
            scene_num = int(match.group(1))
            # Remove #n# from clean heading text
            clean_text = stripped[: match.start()].strip()
        else:
            clean_text = stripped

        if is_forced_heading and clean_text.startswith("."):
            clean_text = clean_text[1:].strip()

        return True, scene_num, clean_text

    @classmethod
    def parse(cls, content: str) -> ParsedScript:
        """Parse raw Fountain screenplay text into a ParsedScript instance."""
        sanitized = sanitize_fountain_text(content)
        lines = sanitized.splitlines()

        # Detect script language for narrator cue name: same test used by
        # _wrap_bare_narration in vertex_analysis.py and narratorSpeakerName()
        # in the frontend so all three paths agree on the cue name.
        _has_japanese = any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" for ch in sanitized)
        narrator_cue_name = "ナレーター" if _has_japanese else "Narrator"

        metadata = ScriptMetadata()
        in_title_page = True
        title_page_lines: list[str] = []

        script_lines: list[str] = []
        outline_lines: list[str] = []

        i = 0
        total_lines = len(lines)

        # 1. Parse Title Page Key-Values
        while i < total_lines and in_title_page:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                # Blank line may mark the end of the title page
                # If we have collected key-values, title page ends
                if metadata.raw_key_values or title_page_lines:
                    in_title_page = False
                i += 1
                continue

            # Check if this line is already a scene heading or section start
            is_heading, _, _ = cls.parse_scene_heading(stripped)
            if is_heading or stripped.startswith("#"):
                in_title_page = False
                break

            if ":" in stripped:
                key, val = stripped.split(":", 1)
                clean_key = key.strip().lower()
                clean_val = val.strip()
                metadata.raw_key_values[clean_key] = clean_val

                if clean_key == "title":
                    metadata.title = clean_val
                elif clean_key in ("logline", "log line"):
                    metadata.logline = clean_val
                elif clean_key == "synopsis":
                    metadata.synopsis = clean_val
                elif clean_key == "format":
                    metadata.format = clean_val
                elif clean_key in ("genre", "genre1"):
                    metadata.genre1 = clean_val
                elif clean_key == "genre2":
                    metadata.genre2 = clean_val
                elif clean_key in ("characters", "cast"):
                    names = [n.strip() for n in clean_val.split(",") if n.strip()]
                    metadata.characters.extend(names)
                else:
                    title_page_lines.append(stripped)
            else:
                title_page_lines.append(stripped)

            i += 1

        # 2. Parse Remaining Lines into Scenes and Dialogue
        scenes: list[ParsedScene] = []
        current_scene: ParsedScene | None = None
        current_scene_lines: list[str] = []
        current_character: str | None = None
        current_dialogue_lines: list[str] = []
        current_parenthetical: str = ""
        current_action_lines: list[str] = []
        auto_scene_number = 1

        def flush_action() -> None:
            nonlocal current_action_lines, current_scene
            if current_action_lines and current_scene:
                action_text = " ".join(current_action_lines).strip()
                if action_text:
                    current_scene.utterances.append(
                        UtteranceItem(
                            scene_number=current_scene.scene_number,
                            utterance_index=len(current_scene.utterances),
                            speaker=narrator_cue_name,
                            target_type="narrator",
                            text=action_text,
                        )
                    )
            current_action_lines = []

        def flush_dialogue() -> None:
            nonlocal current_character, current_dialogue_lines, current_parenthetical, current_scene
            if current_character and current_dialogue_lines and current_scene:
                speech = " ".join(current_dialogue_lines).strip()
                if speech:
                    current_scene.dialogues.append(DialogueItem(character=current_character, line=speech))
                    # "@ナレーター" (Japanese) or "@Narrator" / "@NARRATOR" (English) cues
                    # are narration voiced by the narrator, not a character.
                    is_narrator = current_character in NARRATOR_SPEAKERS
                    current_scene.utterances.append(
                        UtteranceItem(
                            scene_number=current_scene.scene_number,
                            utterance_index=len(current_scene.utterances),
                            speaker=current_character,
                            target_type="narrator" if is_narrator else "character",
                            text=speech,
                            performance_direction=current_parenthetical,
                        )
                    )
            current_character = None
            current_dialogue_lines = []
            current_parenthetical = ""

        def flush_scene() -> None:
            nonlocal current_scene, current_scene_lines
            if current_scene:
                flush_dialogue()
                flush_action()
                current_scene.text = "\n".join(current_scene_lines).strip()
                scenes.append(current_scene)
            current_scene = None
            current_scene_lines = []

        while i < total_lines:
            raw_line = lines[i]
            stripped = raw_line.strip()

            if not stripped:
                # A few Fountain writers put a blank line between a
                # parenthetical and its spoken line. Keep that cue alive until
                # the line arrives so the direction is not mistaken for the
                # audition text or discarded as an empty dialogue block.
                if current_parenthetical and current_character and not current_dialogue_lines:
                    flush_action()
                else:
                    flush_dialogue()
                    flush_action()
                if current_scene:
                    current_scene_lines.append("")
                i += 1
                continue

            # Check for scene heading
            is_heading, explicit_num, clean_heading = cls.parse_scene_heading(stripped)
            if is_heading:
                flush_scene()
                scene_num = explicit_num if explicit_num is not None else auto_scene_number
                auto_scene_number = max(auto_scene_number, scene_num) + 1
                current_scene = ParsedScene(scene_number=scene_num, heading=clean_heading)
                current_scene_lines.append(clean_heading)
                script_lines.append(clean_heading)
                i += 1
                continue

            # Section headers (# Section)
            if stripped.startswith("#"):
                outline_lines.append(stripped)
                i += 1
                continue

            # If inside a scene, detect character cue vs dialogue vs action
            if current_scene:
                current_scene_lines.append(stripped)
                script_lines.append(stripped)

                # Character cue heuristic:
                # 1. Starts with @ (Fountain forced character cue)
                # 2. Or uppercase name
                is_forced_char = stripped.startswith("@")
                is_all_caps_char = is_ascii_character_cue(stripped) and not stripped.startswith("!")

                if is_forced_char or is_all_caps_char:
                    flush_action()
                    flush_dialogue()
                    char_name = stripped[1:].strip() if is_forced_char else stripped
                    char_name = re.sub(r"\s*\(.*?\)$", "", char_name).strip()
                    current_character = char_name
                elif current_character:
                    # Parenthetical: a performance direction, e.g. (落ち着いた低い声で).
                    # Captured as TTS guidance (not spoken verbatim). Only the first
                    # parenthetical of a dialogue block is retained.
                    if is_parenthetical_line(stripped):
                        if not current_parenthetical:
                            current_parenthetical = stripped[1:-1].strip()
                    else:
                        current_dialogue_lines.append(stripped)
                else:
                    action_line = stripped[1:].strip() if stripped.startswith("!") else stripped
                    if action_line:
                        current_action_lines.append(action_line)
            else:
                outline_lines.append(stripped)

            i += 1

        flush_scene()

        if not metadata.title and outline_lines:
            metadata.title = outline_lines[0].replace("#", "").strip()

        return ParsedScript(
            metadata=metadata,
            scenes=scenes,
            raw_outline="\n".join(outline_lines).strip(),
            raw_script_body="\n".join(script_lines).strip(),
            source_fountain=sanitized,
        )
