"""Regression coverage for the 10-dimensional shape-and-slope Valence vector."""

from __future__ import annotations

import math

import pytest

from narravant.core.valence_vector import (
    ValenceVectorizationError,
    ValenceVectorizer,
    load_valence_vectorization_profile,
)

PROFILE = {
    "algorithm": "shape_slope_dct",
    "profile_revision": 2,
    "value_min": 1,
    "value_max": 7,
    "dimensions": 10,
    "canonical_points": 64,
    "interpolation": "pchip",
    "dct_type": 2,
    "dct_norm": "ortho",
    "slope_method": "first_difference",
    "level_dimensions": 6,
    "slope_dimensions": 3,
    "level_weight": 0.7,
    "slope_weight": 0.3,
    "flat_range_epsilon": 0.5,
}


@pytest.fixture
def vectorizer() -> ValenceVectorizer:
    return ValenceVectorizer(load_valence_vectorization_profile(PROFILE))


def test_vectorizer_returns_a_finite_unit_10d_vector(vectorizer: ValenceVectorizer) -> None:
    vector = vectorizer.vectorize([1.0, 3.0, 2.0, 6.0, 7.0])

    assert len(vector) == 10
    assert all(math.isfinite(value) for value in vector)
    assert math.isclose(math.fsum(value * value for value in vector), 1.0, abs_tol=1e-12)


def test_flat_curve_uses_the_dedicated_dimension(vectorizer: ValenceVectorizer) -> None:
    assert vectorizer.vectorize([4.0, 4.2, 4.5]) == [0.0] * 9 + [1.0]


def test_out_of_range_valence_is_rejected(vectorizer: ValenceVectorizer) -> None:
    with pytest.raises(ValenceVectorizationError):
        vectorizer.vectorize([0.0, 4.0])
