"""Deterministic resolution of scene-level emotion arcs into chart points."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, floor


@dataclass(frozen=True)
class EmotionArcPointMapping:
    """One chart point's inclusive source-scene range."""

    point_number: int
    start_scene_number: int
    end_scene_number: int
    representative_scene_number: int


def build_scene_mapping(scene_count: int, max_points: int) -> list[EmotionArcPointMapping]:
    """Partition all scenes into at most ``max_points`` deterministic bins."""
    if scene_count < 1:
        raise ValueError("scene_count must be at least 1")
    if max_points < 1:
        raise ValueError("max_points must be at least 1")

    point_count = min(scene_count, max_points)
    return [
        EmotionArcPointMapping(
            point_number=index + 1,
            start_scene_number=(index * scene_count) // point_count + 1,
            end_scene_number=((index + 1) * scene_count) // point_count,
            representative_scene_number=_lower_midpoint(
                (index * scene_count) // point_count + 1,
                ((index + 1) * scene_count) // point_count,
            ),
        )
        for index in range(point_count)
    ]


def scene_mapping_payload(scene_count: int, max_points: int) -> list[dict[str, int]]:
    """Serialize the deterministic mapping for canonical storage and API responses."""
    return [
        {
            "point_number": point.point_number,
            "start_scene_number": point.start_scene_number,
            "end_scene_number": point.end_scene_number,
            "representative_scene_number": point.representative_scene_number,
        }
        for point in build_scene_mapping(scene_count, max_points)
    ]


def aggregate_story_arc(values: Sequence[int], mapping: Sequence[EmotionArcPointMapping]) -> list[int]:
    """Average each source bin and round halves away from zero."""
    _validate_values_match_mapping(values, mapping)
    return [_round_half_away_from_zero(_mean(_values_for_point(values, point))) for point in mapping]


def aggregate_character_arc(values: Sequence[int], mapping: Sequence[EmotionArcPointMapping]) -> list[int]:
    """Average present character values while preserving an all-absent bin as zero."""
    _validate_values_match_mapping(values, mapping)
    aggregated: list[int] = []
    for point in mapping:
        present_values = [value for value in _values_for_point(values, point) if value > 0]
        aggregated.append(0 if not present_values else _round_half_away_from_zero(_mean(present_values)))
    return aggregated


def _lower_midpoint(start_scene_number: int, end_scene_number: int) -> int:
    return start_scene_number + (end_scene_number - start_scene_number) // 2


def _values_for_point(values: Sequence[int], point: EmotionArcPointMapping) -> Sequence[int]:
    return values[point.start_scene_number - 1 : point.end_scene_number]


def _validate_values_match_mapping(values: Sequence[int], mapping: Sequence[EmotionArcPointMapping]) -> None:
    if not mapping:
        raise ValueError("mapping must contain at least one point")
    if len(values) != mapping[-1].end_scene_number:
        raise ValueError("arc values must match the source scene count")


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values)


def _round_half_away_from_zero(value: float) -> int:
    return floor(value + 0.5) if value >= 0 else ceil(value - 0.5)
