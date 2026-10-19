"""Fountain inline speech notation parsing and speaker projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SpeechToken:
    kind: Literal["text", "vocal", "pause", "reaction"]
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class SpeechNotation:
    tokens: tuple[SpeechToken, ...]
    listener: str | None


class SpeechNotationError(ValueError):
    """Invalid Fountain speech notation with an offset in its source text."""

    def __init__(
        self,
        reason: str,
        *,
        offset: int,
        line_number: int | None = None,
        scene_number: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.offset = offset
        self.line_number = line_number
        self.scene_number = scene_number


_LISTENER_PREFIX = "[[listener:"
_PAUSE_TAGS = frozenset({"short pause", "long pause"})
_VOCAL_TAGS = frozenset(
    {
        "breath",
        "sigh",
        "laugh",
        "chuckle",
        "gasp",
        "sob",
        "cough",
        "throat-clearing",
    }
)
_ALLOWED_TAGS = _PAUSE_TAGS | _VOCAL_TAGS


def is_listener_note_candidate(line: str) -> bool:
    """Return whether a line attempts to use the reserved listener-note prefix."""
    return line.strip().lower().startswith("[[listener:")


def parse_listener_note(line: str) -> str:
    """Extract the non-empty speaker name from one exact listener Note line."""
    trimmed = line.strip()
    base_offset = len(line) - len(line.lstrip())
    if "\n" in line or "\r" in line:
        raise SpeechNotationError("listener_note_must_be_single_line", offset=base_offset)
    if not trimmed.startswith(_LISTENER_PREFIX):
        reason = "invalid_listener_note" if is_listener_note_candidate(trimmed) else "not_listener_note"
        raise SpeechNotationError(reason, offset=base_offset)
    if not trimmed.endswith("]]"):
        raise SpeechNotationError("unclosed_listener_note", offset=base_offset + len(trimmed))

    raw_name = trimmed[len(_LISTENER_PREFIX) : -2]
    name_offset = base_offset + len(_LISTENER_PREFIX)
    if "[" in raw_name or "]" in raw_name:
        bracket_offset = min(
            (index for index, character in enumerate(raw_name) if character in "[]"),
            default=0,
        )
        raise SpeechNotationError("invalid_listener_name", offset=name_offset + bracket_offset)
    listener = raw_name.strip()
    if not listener:
        raise SpeechNotationError("empty_listener", offset=name_offset)
    return listener


def _parse_text_segment(
    text: str,
    *,
    base_offset: int,
    strict_unknown_tags: bool,
) -> list[SpeechToken]:
    tokens: list[SpeechToken] = []
    segment_start = 0
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "<":
            cursor += 1
            continue
        close = text.find(">", cursor + 1)
        if close < 0:
            if strict_unknown_tags:
                raise SpeechNotationError("unclosed_tag", offset=base_offset + cursor)
            cursor += 1
            continue
        tag_value = text[cursor + 1 : close]
        if tag_value not in _ALLOWED_TAGS:
            if strict_unknown_tags:
                raise SpeechNotationError("unknown_tag", offset=base_offset + cursor)
            cursor = close + 1
            continue
        if segment_start < cursor:
            tokens.append(
                SpeechToken(
                    kind="text",
                    value=text[segment_start:cursor],
                    start=base_offset + segment_start,
                    end=base_offset + cursor,
                )
            )
        tokens.append(
            SpeechToken(
                kind="pause" if tag_value in _PAUSE_TAGS else "vocal",
                value=tag_value,
                start=base_offset + cursor,
                end=base_offset + close + 1,
            )
        )
        cursor = close + 1
        segment_start = cursor
    if segment_start < len(text):
        tokens.append(
            SpeechToken(
                kind="text",
                value=text[segment_start:],
                start=base_offset + segment_start,
                end=base_offset + len(text),
            )
        )
    return tokens


def _parse_speech_notation(
    text: str,
    listener: str | None,
    *,
    strict_unknown_tags: bool,
) -> SpeechNotation:
    if listener is not None:
        listener = listener.strip()
        if not listener:
            raise SpeechNotationError("empty_listener", offset=0)
        if any(character in listener for character in "\r\n[]"):
            raise SpeechNotationError("invalid_listener_name", offset=0)

    if listener is None:
        return SpeechNotation(
            tokens=tuple(
                _parse_text_segment(
                    text,
                    base_offset=0,
                    strict_unknown_tags=strict_unknown_tags,
                )
            ),
            listener=None,
        )

    tokens: list[SpeechToken] = []
    cursor = 0
    while cursor < len(text):
        pipe = text.find("|", cursor)
        if pipe < 0:
            tokens.extend(
                _parse_text_segment(
                    text[cursor:],
                    base_offset=cursor,
                    strict_unknown_tags=strict_unknown_tags,
                )
            )
            break
        tokens.extend(
            _parse_text_segment(
                text[cursor:pipe],
                base_offset=cursor,
                strict_unknown_tags=strict_unknown_tags,
            )
        )
        close = text.find("|", pipe + 1)
        if close < 0:
            raise SpeechNotationError("unclosed_pipe", offset=pipe)
        reaction_text = text[pipe + 1 : close]
        reaction_tokens = _parse_text_segment(
            reaction_text,
            base_offset=pipe + 1,
            strict_unknown_tags=strict_unknown_tags,
        )
        if not any(token.kind == "text" and token.value.strip() for token in reaction_tokens):
            raise SpeechNotationError("empty_reaction", offset=pipe)
        tokens.append(
            SpeechToken(
                kind="reaction",
                value=reaction_text,
                start=pipe,
                end=close + 1,
            )
        )
        cursor = close + 1

    if not any(token.kind == "text" and token.value.strip() for token in tokens if token.kind != "reaction"):
        raise SpeechNotationError("missing_primary_speech", offset=0)
    if not any(token.kind == "reaction" for token in tokens):
        raise SpeechNotationError("listener_note_without_reaction", offset=0)
    return SpeechNotation(tokens=tuple(tokens), listener=listener)


def parse_speech_notation(text: str, listener: str | None = None) -> SpeechNotation:
    """Tokenize speech while preserving legacy pipes and unknown tags without activation."""
    return _parse_speech_notation(
        text,
        listener,
        strict_unknown_tags=listener is not None,
    )


def validate_generated_speech_notation(text: str, listener: str | None = None) -> SpeechNotation:
    """生成発話を厳密に検証し、タグだけの発話も拒否する。"""
    if listener is None and "|" in text:
        raise SpeechNotationError("pipe_requires_listener", offset=text.index("|"))
    notation = _parse_speech_notation(text, listener, strict_unknown_tags=True)
    if not spoken_turns("", notation):
        raise SpeechNotationError("missing_speech", offset=0)
    return notation


def _spoken_text(tokens: tuple[SpeechToken, ...] | list[SpeechToken]) -> str:
    return "".join(token.value for token in tokens if token.kind == "text")


def spoken_turns(speaker: str, notation: SpeechNotation) -> tuple[tuple[str, str], ...]:
    """Project speech text into its audible speaker order, omitting control tags."""
    turns: list[tuple[str, str]] = []
    primary_parts: list[str] = []

    def flush_primary() -> None:
        text = "".join(primary_parts)
        if text.strip():
            turns.append((speaker, text))
        primary_parts.clear()

    for token in notation.tokens:
        if token.kind != "reaction":
            if token.kind == "text":
                primary_parts.append(token.value)
            continue
        flush_primary()
        if notation.listener is None:
            primary_parts.append(f"|{token.value}|")
            continue
        reaction_tokens = _parse_text_segment(
            token.value,
            base_offset=token.start + 1,
            strict_unknown_tags=True,
        )
        reaction_text = _spoken_text(reaction_tokens)
        if reaction_text.strip():
            turns.append((notation.listener, reaction_text))
    flush_primary()
    return tuple(turns)


def spoken_text_by_speaker(speaker: str, notation: SpeechNotation) -> dict[str, str]:
    """Join each speaker's audition text without tags, Notes, or other speakers."""
    text_by_speaker: dict[str, list[str]] = {}
    for turn_speaker, text in spoken_turns(speaker, notation):
        text_by_speaker.setdefault(turn_speaker, []).append(text)
    return {name: "".join(parts) for name, parts in text_by_speaker.items()}
