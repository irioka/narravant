"""Fountain screenplay parser for NARRAVANT.

Parses Fountain syntax plain text into structured screenplay documents,
extracting title page metadata, scene headings, scene numbers, action lines,
and character dialogues according to the v1 JSON schema specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from narravant.core.speech_notation import (
    SpeechNotation,
    SpeechNotationError,
    is_listener_note_candidate,
    parse_listener_note,
    parse_speech_notation,
    spoken_turns,
)

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
_SPOKEN_SCENE_HEADING_PREFIX_REGEX = re.compile(
    r"^(?:INT\./EXT\.?|INT/EXT\.?|I/E\.?|INT\.?|EXT\.?|EST\.?)(?:\s+|$)",
    re.IGNORECASE,
)

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
    listener: str | None = None
    speech_notation: SpeechNotation | None = field(default=None, repr=False, compare=False)
    source_line: int | None = field(default=None, repr=False, compare=False)

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
        """Return non-narrator speakers present in the analysis dialogue projection."""
        seen: list[str] = []
        for scene in self.scenes:
            for dialogue in scene.dialogues:
                if dialogue.character in NARRATOR_SPEAKERS:
                    continue
                if dialogue.character and dialogue.character not in seen:
                    seen.append(dialogue.character)
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

    @staticmethod
    def spoken_scene_heading(heading: str) -> str:
        """Return the scene-heading content intended for narrator speech."""
        without_number = _SCENE_NUMBER_REGEX.sub("", heading).strip()
        without_classification = _SPOKEN_SCENE_HEADING_PREFIX_REGEX.sub("", without_number, count=1)
        return without_classification.strip()

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
                if metadata.raw_key_values or title_page_lines:
                    in_title_page = False
                i += 1
                continue

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
                    names = [name.strip() for name in clean_val.split(",") if name.strip()]
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
        current_dialogue_line_numbers: list[int] = []
        current_dialogue_start_line: int | None = None
        current_parenthetical = ""
        current_listener: str | None = None
        current_listener_line: int | None = None
        current_action_lines: list[str] = []
        current_action_start_line: int | None = None
        in_regular_note = False
        auto_scene_number = 1

        def flush_action() -> None:
            nonlocal current_action_lines, current_action_start_line, current_scene
            if current_action_lines and current_scene:
                action_text = " ".join(current_action_lines).strip()
                if action_text:
                    try:
                        notation = parse_speech_notation(action_text)
                    except SpeechNotationError as exc:
                        exc.line_number = current_action_start_line
                        exc.scene_number = current_scene.scene_number
                        raise
                    current_scene.utterances.append(
                        UtteranceItem(
                            scene_number=current_scene.scene_number,
                            utterance_index=len(current_scene.utterances),
                            speaker=narrator_cue_name,
                            target_type="narrator",
                            text=action_text,
                            speech_notation=notation,
                            source_line=current_action_start_line,
                        )
                    )
            current_action_lines = []
            current_action_start_line = None

        def flush_dialogue() -> None:
            nonlocal current_character
            nonlocal current_dialogue_lines, current_dialogue_line_numbers, current_dialogue_start_line
            nonlocal current_parenthetical, current_listener, current_listener_line, current_scene
            if current_character and current_listener is not None and not current_dialogue_lines and current_scene:
                raise SpeechNotationError(
                    "listener_note_without_speech",
                    offset=0,
                    line_number=current_listener_line,
                    scene_number=current_scene.scene_number,
                )
            if current_character and current_dialogue_lines and current_scene:
                speech = " ".join(current_dialogue_lines).strip()
                if speech:
                    try:
                        notation = parse_speech_notation(speech, current_listener)
                    except SpeechNotationError as exc:
                        offset = max(0, exc.offset)
                        consumed = 0
                        error_line = current_dialogue_start_line or 1
                        for line_number, dialogue_line in zip(
                            current_dialogue_line_numbers,
                            current_dialogue_lines,
                            strict=False,
                        ):
                            if offset <= consumed + len(dialogue_line):
                                error_line = line_number
                                break
                            consumed += len(dialogue_line) + 1
                        exc.line_number = error_line
                        exc.scene_number = current_scene.scene_number
                        raise

                    is_narrator = current_character in NARRATOR_SPEAKERS
                    if current_listener and normalize_speaker_name(current_listener) == normalize_speaker_name(
                        current_character
                    ):
                        raise SpeechNotationError(
                            "listener_is_primary_speaker",
                            offset=0,
                            line_number=current_listener_line or current_dialogue_start_line,
                            scene_number=current_scene.scene_number,
                        )

                    for turn_speaker, turn_text in spoken_turns(current_character, notation):
                        if turn_text.strip():
                            current_scene.dialogues.append(DialogueItem(character=turn_speaker, line=turn_text))
                    current_scene.utterances.append(
                        UtteranceItem(
                            scene_number=current_scene.scene_number,
                            utterance_index=len(current_scene.utterances),
                            speaker=current_character,
                            target_type="narrator" if is_narrator else "character",
                            text=speech,
                            performance_direction=current_parenthetical,
                            listener=current_listener,
                            speech_notation=notation,
                            source_line=current_dialogue_start_line,
                        )
                    )
            current_character = None
            current_dialogue_lines = []
            current_dialogue_line_numbers = []
            current_dialogue_start_line = None
            current_parenthetical = ""
            current_listener = None
            current_listener_line = None

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

            if in_regular_note:
                if current_scene:
                    current_scene_lines.append(stripped)
                    script_lines.append(stripped)
                if "]]" in stripped:
                    in_regular_note = False
                i += 1
                continue

            if not stripped:
                if current_character and not current_dialogue_lines:
                    flush_action()
                else:
                    flush_dialogue()
                    flush_action()
                if current_scene:
                    current_scene_lines.append("")
                i += 1
                continue

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

            if stripped.startswith("#"):
                outline_lines.append(stripped)
                i += 1
                continue

            if current_scene:
                current_scene_lines.append(stripped)
                script_lines.append(stripped)
                if is_listener_note_candidate(stripped) and current_character is None:
                    raise SpeechNotationError(
                        "listener_note_without_speaker",
                        offset=0,
                        line_number=i + 1,
                        scene_number=current_scene.scene_number,
                    )
                if stripped.startswith("[[") and not is_listener_note_candidate(stripped):
                    in_regular_note = "]]" not in stripped
                    i += 1
                    continue
                is_forced_char = stripped.startswith("@")
                is_all_caps_char = is_ascii_character_cue(stripped) and not stripped.startswith("!")

                if is_forced_char or is_all_caps_char:
                    flush_action()
                    flush_dialogue()
                    char_name = stripped[1:].strip() if is_forced_char else stripped
                    char_name = re.sub(r"\s*\(.*?\)$", "", char_name).strip()
                    current_character = char_name
                elif current_character:
                    if is_listener_note_candidate(stripped):
                        try:
                            listener = parse_listener_note(stripped)
                        except SpeechNotationError as exc:
                            exc.line_number = i + 1
                            exc.scene_number = current_scene.scene_number
                            raise
                        if current_dialogue_lines:
                            raise SpeechNotationError(
                                "listener_note_after_speech",
                                offset=0,
                                line_number=i + 1,
                                scene_number=current_scene.scene_number,
                            )
                        if current_listener is not None:
                            raise SpeechNotationError(
                                "multiple_listener_notes",
                                offset=0,
                                line_number=i + 1,
                                scene_number=current_scene.scene_number,
                            )
                        if normalize_speaker_name(listener) == normalize_speaker_name(current_character):
                            raise SpeechNotationError(
                                "listener_is_primary_speaker",
                                offset=0,
                                line_number=i + 1,
                                scene_number=current_scene.scene_number,
                            )
                        current_listener = listener
                        current_listener_line = i + 1
                    elif is_parenthetical_line(stripped):
                        if current_listener is not None:
                            raise SpeechNotationError(
                                "listener_note_before_parenthetical",
                                offset=0,
                                line_number=i + 1,
                                scene_number=current_scene.scene_number,
                            )
                        if not current_parenthetical:
                            current_parenthetical = stripped[1:-1].strip()
                    else:
                        if current_dialogue_start_line is None:
                            current_dialogue_start_line = i + 1
                        current_dialogue_lines.append(stripped)
                        current_dialogue_line_numbers.append(i + 1)
                else:
                    action_line = stripped[1:].strip() if stripped.startswith("!") else stripped
                    if action_line:
                        if current_action_start_line is None:
                            current_action_start_line = i + 1
                        current_action_lines.append(action_line)
            else:
                outline_lines.append(stripped)
            i += 1

        flush_scene()

        if not metadata.title and outline_lines:
            metadata.title = outline_lines[0].replace("#", "").strip()

        parsed = ParsedScript(
            metadata=metadata,
            scenes=scenes,
            raw_outline="\n".join(outline_lines).strip(),
            raw_script_body="\n".join(script_lines).strip(),
            source_fountain=sanitized,
        )

        source_speakers = list(
            dict.fromkeys(
                utterance.speaker for utterance in parsed.all_utterances() if utterance.target_type == "character"
            )
        )
        for scene in parsed.scenes:
            for utterance in scene.utterances:
                if utterance.listener is None:
                    continue
                normalized_listener = normalize_speaker_name(utterance.listener)
                matches = [
                    speaker for speaker in source_speakers if normalize_speaker_name(speaker) == normalized_listener
                ]
                if len(matches) > 1:
                    raise SpeechNotationError(
                        "ambiguous_listener",
                        offset=0,
                        line_number=utterance.source_line,
                        scene_number=utterance.scene_number,
                    )
                listener = matches[0] if matches else utterance.listener
                if normalize_speaker_name(listener) == normalize_speaker_name(utterance.speaker):
                    raise SpeechNotationError(
                        "listener_is_primary_speaker",
                        offset=0,
                        line_number=utterance.source_line,
                        scene_number=utterance.scene_number,
                    )
                utterance.listener = listener
                if utterance.speech_notation is not None:
                    utterance.speech_notation = SpeechNotation(
                        tokens=utterance.speech_notation.tokens,
                        listener=listener,
                    )
                for dialogue in scene.dialogues:
                    if normalize_speaker_name(dialogue.character) == normalized_listener:
                        dialogue.character = listener
        return parsed
