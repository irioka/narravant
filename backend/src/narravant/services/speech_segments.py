"""Fountain-aware TTS segmentation and per-segment voice selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from narravant.core.speech_notation import SpeechToken, parse_speech_notation

if TYPE_CHECKING:
    from narravant.services.playback import PlannedUtterance


MAX_TTS_SEGMENT_CHARACTERS = 180
_SENTENCE_ENDINGS = frozenset("。！？!?…")
_CLAUSE_ENDINGS = frozenset("、，,;；:")
_CLOSING_PUNCTUATION = frozenset('」』）)]}’”\"〉》')
_NON_TERMINAL_ABBREVIATIONS = frozenset({"dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "e.g.", "i.e."})
_TAG_SPELLINGS = (
    "<short pause>",
    "<long pause>",
    "<breath>",
    "<sigh>",
    "<laugh>",
    "<chuckle>",
    "<gasp>",
    "<sob>",
    "<cough>",
    "<throat-clearing>",
)


@dataclass(frozen=True)
class SegmentVoice:
    """One speaker and the voice assigned to that speaker."""

    speaker: str
    voice_id: str


@dataclass(frozen=True)
class SpeechSegment:
    """One sentence-sized provider request with its required speaker voices."""

    text: str
    primary: SegmentVoice
    listener: SegmentVoice | None
    style: str


class SpeechSegmentError(ValueError):
    """A located error while converting one planned utterance to TTS requests."""

    def __init__(
        self,
        reason: str,
        *,
        offset: int,
        scene_number: int,
        utterance_index: int,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.offset = offset
        self.scene_number = scene_number
        self.utterance_index = utterance_index


@dataclass(frozen=True)
class _Atom:
    text: str
    kind: str
    offset: int
    boundary_after: bool = False


def _sentence_end(text: str, index: int) -> int | None:
    character = text[index]
    is_end = character in _SENTENCE_ENDINGS
    if character == ".":
        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        preceding_token = text[: index + 1].rsplit(None, 1)[-1].lower()
        is_end = (
            preceding_token not in _NON_TERMINAL_ABBREVIATIONS
            and not (previous.isdigit() and following.isdigit())
            and (not following or following.isspace() or following in _CLOSING_PUNCTUATION)
        )
    if not is_end:
        return None

    end = index + 1
    while end < len(text) and (text[end] in _SENTENCE_ENDINGS or text[end] in _CLOSING_PUNCTUATION):
        end += 1
    return end


def _split_long_text(text: str, offset: int, final_boundary: bool) -> list[_Atom]:
    atoms: list[_Atom] = []
    remaining = text
    consumed = 0
    while len(remaining) > MAX_TTS_SEGMENT_CHARACTERS:
        limit = MAX_TTS_SEGMENT_CHARACTERS
        clause_positions = [
            position for position, character in enumerate(remaining[:limit]) if character in _CLAUSE_ENDINGS
        ]
        if clause_positions:
            end = clause_positions[-1] + 1
        else:
            next_clause = next(
                (
                    position + 1
                    for position, character in enumerate(remaining[limit:], start=limit)
                    if character in _CLAUSE_ENDINGS
                ),
                None,
            )
            if next_clause is not None and next_clause <= limit + 30:
                end = next_clause
            else:
                whitespace_positions = [
                    position for position, character in enumerate(remaining[:limit]) if character.isspace()
                ]
                end = whitespace_positions[-1] + 1 if whitespace_positions else limit

        if end <= 0:
            end = limit
        atoms.append(_Atom(remaining[:end], "text", offset + consumed, boundary_after=True))
        remaining = remaining[end:]
        consumed += end
    if remaining:
        atoms.append(_Atom(remaining, "text", offset + consumed, boundary_after=final_boundary))
    return atoms


def _text_atoms(text: str, offset: int) -> list[_Atom]:
    sentences: list[tuple[str, int, bool]] = []
    start = 0
    cursor = 0
    while cursor < len(text):
        end = _sentence_end(text, cursor)
        if end is None:
            cursor += 1
            continue
        sentences.append((text[start:end], offset + start, True))
        start = end
        cursor = end
    if start < len(text):
        sentences.append((text[start:], offset + start, False))

    atoms: list[_Atom] = []
    for sentence, sentence_offset, has_sentence_end in sentences:
        atoms.extend(_split_long_text(sentence, sentence_offset, final_boundary=has_sentence_end))
    return atoms


def _raw_token_text(text: str, token: SpeechToken) -> str:
    return text[token.start : token.end]


def _visible_reaction_length(reaction: str) -> int:
    visible = reaction
    for tag in _TAG_SPELLINGS:
        visible = visible.replace(tag, "")
    return len(visible.strip())


def _has_body(atoms: list[_Atom]) -> bool:
    for atom in atoms:
        if atom.kind == "text" and atom.text.strip():
            return True
        if atom.kind == "reaction":
            inner = atom.text[1:-1]
            if _visible_reaction_length(inner):
                return True
    return False


def build_speech_segments(utterance: PlannedUtterance) -> tuple[SpeechSegment, ...]:
    """Split a planned utterance without cutting tags or reaction spans."""
    notation = utterance.notation or parse_speech_notation(utterance.text)
    source = utterance.text
    atoms: list[_Atom] = []
    for token in notation.tokens:
        if token.kind == "text":
            atoms.extend(_text_atoms(token.value, token.start))
            continue
        raw = _raw_token_text(source, token)
        if token.kind == "reaction":
            if _visible_reaction_length(token.value) > MAX_TTS_SEGMENT_CHARACTERS:
                raise SpeechSegmentError(
                    "reaction_too_long",
                    offset=token.start,
                    scene_number=utterance.scene_number,
                    utterance_index=utterance.utterance_index,
                )
            atoms.append(_Atom(raw, "reaction", token.start))
        else:
            atoms.append(_Atom(raw, token.kind, token.start))

    previous_pause_offset: int | None = None
    for atom in atoms:
        if atom.kind == "pause":
            if previous_pause_offset is not None:
                raise SpeechSegmentError(
                    "adjacent_pause_tags",
                    offset=atom.offset,
                    scene_number=utterance.scene_number,
                    utterance_index=utterance.utterance_index,
                )
            previous_pause_offset = atom.offset
        elif atom.kind in {"text", "reaction"} and (
            atom.text.strip() if atom.kind == "text" else _visible_reaction_length(atom.text[1:-1])
        ):
            previous_pause_offset = None
        elif atom.kind == "vocal":
            previous_pause_offset = None

    if not _has_body(atoms):
        raise SpeechSegmentError(
            "missing_speech",
            offset=0,
            scene_number=utterance.scene_number,
            utterance_index=utterance.utterance_index,
        )

    primary = SegmentVoice(utterance.speaker, utterance.voice_id)
    segments: list[SpeechSegment] = []
    current: list[_Atom] = []
    pending_vocal: list[_Atom] = []
    boundary_pending = False

    def flush() -> None:
        nonlocal current
        rendered = "".join(atom.text for atom in current).strip()
        if not rendered or not _has_body(current):
            current = []
            return
        has_reaction = any(atom.kind == "reaction" for atom in current)
        listener_voice = utterance.listener_voice if has_reaction else None
        if has_reaction and listener_voice is None:
            reaction = next(atom for atom in current if atom.kind == "reaction")
            raise SpeechSegmentError(
                "missing_listener_voice",
                offset=reaction.offset,
                scene_number=utterance.scene_number,
                utterance_index=utterance.utterance_index,
            )
        segments.append(SpeechSegment(rendered, primary, listener_voice, utterance.performance_direction))
        current = []

    for atom in atoms:
        if atom.kind == "vocal" and boundary_pending:
            pending_vocal.append(atom)
            continue
        if atom.kind == "text" and atom.text.strip() and boundary_pending and _has_body(current):
            flush()
            current.extend(pending_vocal)
            pending_vocal.clear()
            boundary_pending = False
        elif atom.kind == "text" and not atom.text.strip() and boundary_pending:
            current.append(atom)
            continue
        else:
            if pending_vocal:
                current.extend(pending_vocal)
                pending_vocal.clear()
        current.append(atom)
        if atom.boundary_after:
            boundary_pending = True

    if pending_vocal:
        current.extend(pending_vocal)
    flush()
    return tuple(segments)
