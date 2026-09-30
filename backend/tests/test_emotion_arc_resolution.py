"""Behavioral contract tests for deterministic Emotion Arc resolution."""

from __future__ import annotations

import pytest

from narravant.core.emotion_arc_resolution import (
    aggregate_character_arc,
    aggregate_story_arc,
    build_scene_mapping,
    scene_mapping_payload,
)


@pytest.mark.parametrize("scene_count", [1, 15, 36, 37, 100, 500])
def test_mapping_partitions_every_scene_without_empty_or_overlapping_bins(scene_count: int) -> None:
    """Removing an original scene or adding an empty bin must fail this test."""
    mapping = build_scene_mapping(scene_count=scene_count, max_points=36)

    assert len(mapping) == min(scene_count, 36)
    assert mapping[0].point_number == 1
    assert mapping[0].start_scene_number == 1
    assert mapping[-1].point_number == len(mapping)
    assert mapping[-1].end_scene_number == scene_count
    assert all(
        point.start_scene_number <= point.representative_scene_number <= point.end_scene_number for point in mapping
    )
    assert [
        scene_number
        for point in mapping
        for scene_number in range(point.start_scene_number, point.end_scene_number + 1)
    ] == list(range(1, scene_count + 1))


def test_mapping_uses_lower_midpoint_for_an_even_width_bin() -> None:
    """Changing representative selection to an upper midpoint or an arc extremum must fail."""
    mapping = build_scene_mapping(scene_count=37, max_points=36)

    assert mapping[-1].start_scene_number == 36
    assert mapping[-1].end_scene_number == 37
    assert mapping[-1].representative_scene_number == 36


def test_mapping_payload_is_the_canonical_api_and_storage_representation() -> None:
    """Changing a mapping field or omitting the 1:1 form must fail this test."""
    assert scene_mapping_payload(scene_count=2, max_points=36) == [
        {
            "point_number": 1,
            "start_scene_number": 1,
            "end_scene_number": 1,
            "representative_scene_number": 1,
        },
        {
            "point_number": 2,
            "start_scene_number": 2,
            "end_scene_number": 2,
            "representative_scene_number": 2,
        },
    ]


def test_story_aggregation_rounds_positive_and_negative_halves_away_from_zero() -> None:
    """Changing to Python banker's rounding must fail this test."""
    mapping = build_scene_mapping(scene_count=2, max_points=1)

    assert aggregate_story_arc([1, 2], mapping) == [2]
    assert aggregate_story_arc([-1, 0], mapping) == [-1]
    assert aggregate_story_arc([0, 1], mapping) == [1]


def test_character_aggregation_excludes_absent_zero_unless_all_scenes_are_absent() -> None:
    """Including zero in a character average must fail this test."""
    mapping = build_scene_mapping(scene_count=2, max_points=1)

    assert aggregate_character_arc([7, 0], mapping) == [7]
    assert aggregate_character_arc([0, 0], mapping) == [0]
