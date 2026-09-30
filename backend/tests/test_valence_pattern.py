"""Valence pattern control-point configuration and similarity tests."""

from __future__ import annotations

import pytest

from narravant.core.valence_pattern import (
    ValencePatternConfigurationError,
    load_valence_patterns,
    valence_pattern_similarities,
)
from narravant.core.valence_vector import default_valence_vectorizer


def _entries() -> list[dict[str, object]]:
    return [
        {"id": "n", "name": "N", "description": "desc", "control_values": [1, 7, 1, 7]},
        {"id": "reverse_n", "name": "逆N", "description": "desc", "control_values": [7, 1, 7, 1]},
        {"id": "v", "name": "V", "description": "desc", "control_values": [7, 1, 7]},
        {"id": "reverse_v", "name": "逆V", "description": "desc", "control_values": [1, 7, 1]},
        {"id": "rise", "name": "上昇", "description": "desc", "control_values": [1, 7]},
        {"id": "fall", "name": "下降", "description": "desc", "control_values": [7, 1]},
    ]


def test_load_valence_patterns_accepts_semantic_control_points() -> None:
    patterns = load_valence_patterns(_entries(), value_min=1, value_max=7)

    assert len(patterns) == 6
    assert patterns[0].control_values == (1.0, 7.0, 1.0, 7.0)


def test_load_valence_patterns_rejects_legacy_dense_vectors() -> None:
    entries = _entries()
    entries[0] = {"id": "n", "name": "N", "vector": [1] * 18}

    with pytest.raises(ValencePatternConfigurationError):
        load_valence_patterns(entries, value_min=1, value_max=7)


def test_valence_pattern_similarities_scores_all_patterns() -> None:
    similarities = valence_pattern_similarities(
        [1, 3, 5, 7],
        load_valence_patterns(_entries(), value_min=1, value_max=7),
        default_valence_vectorizer,
    )

    assert {item.pattern_id for item in similarities} == {"n", "reverse_n", "v", "reverse_v", "rise", "fall"}
    assert all(0.0 <= item.percentage <= 100.0 for item in similarities)
