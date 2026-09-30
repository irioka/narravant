"""Stable control-point Valence patterns and injected-vectorizer similarity."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real

from narravant.core.valence_vector import ValenceVectorizer

VALENCE_PATTERN_IDS = ("n", "reverse_n", "v", "reverse_v", "rise", "fall")
CONTROL_POINT_COUNTS = {"n": 4, "reverse_n": 4, "v": 3, "reverse_v": 3, "rise": 2, "fall": 2}


class ValencePatternConfigurationError(ValueError):
    """The single supported Valence pattern configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ValencePattern:
    pattern_id: str
    name: str
    description: str
    control_values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ValencePatternSimilarity:
    pattern_id: str
    name: str
    description: str
    percentage: float


def load_valence_patterns(entries: object, *, value_min: float, value_max: float) -> tuple[ValencePattern, ...]:
    """Validate the six fixed-order patterns without accepting legacy vectors."""
    _validate_value_bounds(value_min, value_max)
    if not isinstance(entries, list) or len(entries) != len(VALENCE_PATTERN_IDS):
        raise ValencePatternConfigurationError("Valence patterns must contain exactly six entries")

    patterns: list[ValencePattern] = []
    expected_keys = {"id", "name", "description", "control_values"}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValencePatternConfigurationError("Valence pattern entry must be a mapping")
        if unknown_keys := set(entry) - expected_keys:
            raise ValencePatternConfigurationError(f"Valence pattern has unknown keys: {sorted(unknown_keys)}")
        if "id" not in entry or "name" not in entry or "control_values" not in entry:
            raise ValencePatternConfigurationError("Valence pattern is missing required fields")
        pattern_id = entry["id"]
        name = entry["name"]
        description = entry.get("description", "")
        control_values = entry["control_values"]
        if not isinstance(pattern_id, str) or pattern_id not in VALENCE_PATTERN_IDS:
            raise ValencePatternConfigurationError("Valence pattern id is missing or unsupported")
        if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
            raise ValencePatternConfigurationError("Valence pattern name is malformed")
        if isinstance(control_values, (str, bytes)) or not isinstance(control_values, Sequence):
            raise ValencePatternConfigurationError("Valence pattern control_values must be a sequence")
        if len(control_values) != CONTROL_POINT_COUNTS[pattern_id]:
            raise ValencePatternConfigurationError(
                f"Valence pattern {pattern_id} must contain {CONTROL_POINT_COUNTS[pattern_id]} control values"
            )
        numeric_values = tuple(_finite_value(value) for value in control_values)
        if any(value < value_min or value > value_max for value in numeric_values):
            raise ValencePatternConfigurationError(
                f"Valence pattern control values must be within {value_min}..{value_max}"
            )
        patterns.append(ValencePattern(pattern_id, name, description, numeric_values))
    if tuple(pattern.pattern_id for pattern in patterns) != VALENCE_PATTERN_IDS:
        raise ValencePatternConfigurationError("Valence pattern ids must be the stable configured order")
    return tuple(patterns)


def valence_pattern_similarities(
    valence: Sequence[float], patterns: Sequence[ValencePattern], vectorizer: ValenceVectorizer
) -> list[ValencePatternSimilarity]:
    """Return descending, clamped 0..100 percentages using the supplied vectorizer."""
    document_vector = vectorizer.vectorize(valence)
    rows = []
    for pattern in patterns:
        similarity = vectorizer.cosine_similarity(document_vector, vectorizer.vectorize(pattern.control_values))
        rows.append(
            ValencePatternSimilarity(
                pattern.pattern_id,
                pattern.name,
                pattern.description,
                round(max(0.0, min(100.0, similarity * 100.0)), 2),
            )
        )
    return sorted(rows, key=lambda row: (-row.percentage, row.pattern_id))


def _validate_value_bounds(value_min: float, value_max: float) -> None:
    if _finite_value(value_min) >= _finite_value(value_max):
        raise ValencePatternConfigurationError("Valence value_min must be less than value_max")


def _finite_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValencePatternConfigurationError("Valence pattern control values must be finite numbers")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValencePatternConfigurationError("Valence pattern control values must be finite numbers")
    return numeric
