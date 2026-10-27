"""完成稿の本文を固定した演技レビュー。中間資料はメモリ内だけで扱う。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from narravant.core.fountain import FountainParser, is_parenthetical_line
from narravant.core.speech_notation import AllowedPerformanceTag, parse_speech_notation
from narravant.services.narrative_adaptation import NarrativeAdaptationError

if TYPE_CHECKING:
    from narravant.services.vertex_analysis import CanonicalAnalysis

logger = logging.getLogger(__name__)
class InlineCue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    offset: int = Field(ge=0)
    tag: AllowedPerformanceTag


class TurnPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scene_number: int = Field(ge=1)
    utterance_index: int = Field(ge=0)
    split_offsets: list[int]
    styles: list[str]
    insertions: list[InlineCue]


class PerformanceReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    edits: list[TurnPerformance]


def candidate_offsets(text: str) -> list[int]:
    """提示した句読点・語境界のみを編集に使う。タグ内部を除く。"""
    tokens = parse_speech_notation(text).tokens
    forbidden = {i for t in tokens if t.kind != "text" for i in range(t.start + 1, t.end)}
    return [
        i
        for i in range(1, len(text))
        if i not in forbidden
        and (text[i - 1].isspace() or text[i].isspace() or text[i - 1] in "。！？.!?、,;；:：…」』）)")
        and not (text[i - 1].isascii() and text[i].isascii() and text[i - 1].isalnum() and text[i].isalnum())
    ]


def _spoken(text: str) -> str:
    return "".join(t.value for t in parse_speech_notation(text).tokens if t.kind == "text")


def apply_performance_review(
    source_fountain: str,
    edits: Sequence[TurnPerformance],
    *,
    preserve_existing_directions: bool = False,
) -> str:
    """元のsliceと許可した制御行だけを描画し、再parseで固定発話を照合する。"""
    if not edits:
        return source_fountain
    parsed = FountainParser.parse(source_fountain)
    turns = {(u.scene_number, u.utterance_index): u for u in parsed.all_utterances()}
    # source_lineはsanitize後の1始まり。先頭空白行の分だけ元稿へ戻す。
    leading = len(source_fountain) - len(source_fountain.lstrip())
    line_shift = source_fountain[:leading].count("\n")
    lines = source_fountain.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []
    expected: dict[tuple[int, int], list[tuple[str, str]]] = {}
    seen = set()
    for edit in edits:
        key = (edit.scene_number, edit.utterance_index)
        if key in seen or key not in turns:
            raise ValueError("performance_invalid_target")
        seen.add(key)
        u = turns[key]
        if u.listener or "|" in u.text:
            raise ValueError("performance_listener_edit")
        offsets = edit.split_offsets
        allowed = candidate_offsets(u.text)
        if offsets != sorted(set(offsets)) or any(i not in allowed for i in offsets):
            raise ValueError("performance_invalid_offset")
        if len(edit.styles) != len(offsets) + 1 or any(
            len(s) > 120 or any(c in s for c in "\r\n()（）[]<>@") for s in edit.styles
        ):
            raise ValueError("performance_invalid_style")
        if (
            preserve_existing_directions
            and u.performance_direction
            and any(s != u.performance_direction for s in edit.styles)
        ):
            raise ValueError("performance_existing_direction")
        insertions = edit.insertions
        if len({c.offset for c in insertions}) != len(insertions) or any(
            c.offset not in [0, *allowed, len(u.text)] for c in insertions
        ):
            raise ValueError("performance_invalid_insertion")
        start = (u.source_line or 1) - 1 + line_shift
        end = start
        collected: list[str] = []
        notes: list[tuple[int, str]] = []
        while end < len(lines):
            value = lines[end].strip()
            if value.startswith("[[") and not value.lower().startswith("[[listener:"):
                note_lines = [lines[end]]
                end += 1
                while "]]" not in note_lines[-1] and end < len(lines):
                    note_lines.append(lines[end])
                    end += 1
                if "]]" not in note_lines[-1]:
                    raise ValueError("performance_source_range")
                position = len(" ".join(collected))
                if notes and notes[-1][0] == position:
                    notes[-1] = (position, notes[-1][1] + "".join(note_lines))
                else:
                    notes.append((position, "".join(note_lines)))
                continue
            if not value or value.startswith("@") or is_parenthetical_line(value):
                break
            candidate = " ".join([*collected, value])
            if u.target_type == "narrator" and value.startswith("!") and not u.text.startswith(candidate):
                value = value[1:].strip()
            collected.append(value)
            end += 1
            if " ".join(collected) == u.text:
                break
        if " ".join(collected) != u.text:
            raise ValueError("performance_source_range")
        while start > 0 and is_parenthetical_line(lines[start - 1].strip()):
            start -= 1
        cuts = [0, *offsets, len(u.text)]
        rendered_texts = []
        blocks = []
        expected[key] = []
        for n, (a, b) in enumerate(zip(cuts, cuts[1:], strict=False)):
            fragment = u.text[a:b]
            if not _spoken(fragment).strip():
                raise ValueError("performance_empty_fragment")
            local_cues = [c for c in insertions if a <= c.offset < b or c.offset == b == len(u.text)]
            for cue in sorted(local_cues, key=lambda c: c.offset, reverse=True):
                at = cue.offset - a
                fragment = fragment[:at] + f"<{cue.tag}>" + fragment[at:]
            notation = parse_speech_notation(fragment)
            events = [t for t in notation.tokens if t.kind != "text" or t.value.strip()]
            if any(x.kind == y.kind == "pause" for x, y in zip(events, events[1:], strict=False)):
                raise ValueError("performance_adjacent_pause")
            style = edit.styles[n]
            expected[key].append((fragment.strip(), style))
            rendered_texts.append(fragment)
            for position, note in reversed(notes):
                if a <= position < b:
                    at = position - a + sum(len(c.tag) + 2 for c in local_cues if c.offset <= position)
                    if fragment[at : at + 1] != " ":
                        raise ValueError("performance_note_boundary")
                    fragment = fragment[:at] + "\n" + note + fragment[at + 1 :]
            has_existing_cue = n == 0 and start > 0 and lines[start - 1].strip() in {f"@{u.speaker}", u.speaker}
            cue = "" if has_existing_cue else f"@{u.speaker}\n"
            blocks.append(cue + (f"({style})\n" if style else "") + fragment)
        events = [
            t for t in parse_speech_notation("".join(rendered_texts)).tokens if t.kind != "text" or t.value.strip()
        ]
        if any(x.kind == y.kind == "pause" for x, y in zip(events, events[1:], strict=False)):
            raise ValueError("performance_adjacent_pause")
        # 元cueは残る。空cueに続く同名cueはparserで発話を増やさない。
        replacements.append((start, end, "\n\n".join(blocks) + "\n"))
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement]
    result = "".join(lines)
    after = FountainParser.parse(result)
    if [(s.scene_number, s.heading) for s in parsed.scenes] != [(s.scene_number, s.heading) for s in after.scenes]:
        raise ValueError("performance_scene_changed")
    for old_scene, new_scene in zip(parsed.scenes, after.scenes, strict=True):
        projected = []
        for u in old_scene.utterances:
            key = (u.scene_number, u.utterance_index)
            projected.extend(
                (u.speaker, text, style) for text, style in expected.get(key, [(u.text, u.performance_direction)])
            )
        if projected != [(u.speaker, u.text, u.performance_direction) for u in new_scene.utterances]:
            raise ValueError("performance_text_changed")
    return result


@dataclass(frozen=True)
class PerformanceContext:
    work_summary: str
    scene_intents: dict[int, str]


@dataclass(frozen=True)
class PerformanceReviewConfig:
    max_attempts: int = 2
    window_max_characters: int = 24000


class PerformanceGenerationClient(Protocol):
    def summarize_performance_window(
        self,
        window_fountain: str,
        scene_numbers: Sequence[int],
        analysis: Any,
        *,
        deadline: float,
        ensure_not_cancelled: Callable[[], None] | None = None,
    ) -> dict[int, str]: ...

    def review_scene_performance(
        self, scene_number: int, utterances: Sequence[Any], context: PerformanceContext, **kwargs: Any
    ) -> PerformanceReviewResult: ...


class PerformanceReviewService:
    def __init__(self, client: PerformanceGenerationClient, config: PerformanceReviewConfig) -> None:
        self.client = client
        self.config = config

    @staticmethod
    def _check(deadline: float, ensure_not_cancelled: Callable[[], None]) -> None:
        ensure_not_cancelled()
        if monotonic() >= deadline:
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    def _request(
        self,
        request: Callable[[], Any],
        validate: Callable[[Any], Any],
        *,
        scene: int,
        deadline: float,
        ensure_not_cancelled: Callable[[], None],
    ) -> Any:
        for attempt in range(min(self.config.max_attempts, 2)):
            self._check(deadline, ensure_not_cancelled)
            try:
                result = validate(request())
                self._check(deadline, ensure_not_cancelled)
                return result
            except ValueError:
                logger.warning("performance_review scene=%d attempt=%d reason=invalid_response", scene, attempt + 1)
        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")

    def build_performance_context(
        self,
        source_fountain: str,
        analysis: CanonicalAnalysis,
        *,
        deadline: float,
        ensure_not_cancelled: Callable[[], None],
    ) -> PerformanceContext:
        parsed = FountainParser.parse(source_fountain)
        intents: dict[int, list[str]] = {s.scene_number: [] for s in parsed.scenes}
        limit = self.config.window_max_characters
        windows: list[tuple[str, list[int]]] = []
        window = ""
        numbers: list[int] = []
        for scene in parsed.scenes:
            for u in scene.utterances:
                text = f"Scene {scene.scene_number} {scene.heading}: @{u.speaker}\n{u.text}\n"
                # 長い発話もタグ外の合法境界で分割する。
                if len(text) > limit and window:
                    windows.append((window, numbers))
                    window, numbers = "", []
                while len(text) > limit:
                    boundaries = [i for i in candidate_offsets(text) if i <= limit]
                    if not boundaries:
                        raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
                    cut = boundaries[-1]
                    windows.append((text[:cut], [scene.scene_number]))
                    text = text[cut:]
                if len(window) + len(text) > limit:
                    windows.append((window, numbers))
                    window, numbers = "", []
                window += text
                if scene.scene_number not in numbers:
                    numbers = [*numbers, scene.scene_number]
            if not scene.utterances:
                windows.append((scene.heading[:limit], [scene.scene_number]))
        if window:
            windows.append((window, numbers))
        for text, scene_numbers in windows:

            def validate(value: dict[int, str], expected=scene_numbers) -> dict[int, str]:
                if set(value) != set(expected) or any(not isinstance(v, str) or not v.strip() for v in value.values()):
                    raise ValueError("performance_context_coverage")
                return value

            values = self._request(
                lambda text=text, numbers=scene_numbers: self.client.summarize_performance_window(
                    text,
                    numbers,
                    analysis,
                    deadline=deadline,
                    ensure_not_cancelled=ensure_not_cancelled,
                ),
                validate,
                scene=0,
                deadline=deadline,
                ensure_not_cancelled=ensure_not_cancelled,
            )
            for number, intent in values.items():
                intents[number].append(intent)
        if any(not value for value in intents.values()):
            raise NarrativeAdaptationError("ADAPTATION_VERIFICATION_FAILED")
        return PerformanceContext(
            json.dumps(
                {
                    "synopsis": analysis.metadata.get("synopsis", ""),
                    "theme": analysis.metadata.get("theme_setting", ""),
                    "turning_points": getattr(analysis, "turning_points", []),
                    "characters": getattr(analysis, "characters", []),
                },
                ensure_ascii=False,
            ),
            {n: "\n".join(v) for n, v in intents.items()},
        )

    def review_performance(
        self,
        source_fountain: str,
        analysis: CanonicalAnalysis,
        *,
        source_cards: Sequence[Any],
        deadline: float,
        ensure_not_cancelled: Callable[[], None],
        on_progress: Callable[[int], None],
        preserve_existing_directions: bool,
    ) -> str:
        context = self.build_performance_context(
            source_fountain, analysis, deadline=deadline, ensure_not_cancelled=ensure_not_cancelled
        )
        scenes = FountainParser.parse(source_fountain).scenes
        edits: list[TurnPerformance] = []
        for index, scene in enumerate(scenes):
            related = (scenes[index - 1].utterances[-2:] if index else []) + (
                scenes[index + 1].utterances[:2] if index + 1 < len(scenes) else []
            )

            def validate(result: PerformanceReviewResult, scene=scene) -> PerformanceReviewResult:
                if any(e.scene_number != scene.scene_number for e in result.edits):
                    raise ValueError("performance_wrong_scene")
                apply_performance_review(
                    source_fountain, [*edits, *result.edits], preserve_existing_directions=preserve_existing_directions
                )
                return result

            result = self._request(
                lambda scene=scene, related=related: self.client.review_scene_performance(
                    scene.scene_number,
                    scene.utterances,
                    context,
                    related_turns=related,
                    source_cards=source_cards,
                    deadline=deadline,
                    preserve_existing_directions=preserve_existing_directions,
                    ensure_not_cancelled=ensure_not_cancelled,
                ),
                validate,
                scene=scene.scene_number,
                deadline=deadline,
                ensure_not_cancelled=ensure_not_cancelled,
            )
            edits.extend(result.edits)
            on_progress(index + 1)
        self._check(deadline, ensure_not_cancelled)
        return apply_performance_review(
            source_fountain, edits, preserve_existing_directions=preserve_existing_directions
        )
