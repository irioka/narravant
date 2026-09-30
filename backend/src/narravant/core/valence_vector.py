"""Configured shape-and-slope vectorization for screenplay emotional arcs."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from numbers import Real

import numpy as np
from scipy.fft import dct
from scipy.interpolate import PchipInterpolator

SUPPORTED_PROFILE_VALUES = {
    "algorithm": "shape_slope_dct",
    "profile_revision": 2,
    "dimensions": 10,
    "canonical_points": 64,
    "interpolation": "pchip",
    "dct_type": 2,
    "dct_norm": "ortho",
    "slope_method": "first_difference",
    "level_dimensions": 6,
    "slope_dimensions": 3,
}

DEFAULT_PROFILE_RAW = {
    **SUPPORTED_PROFILE_VALUES,
    "value_min": 1,
    "value_max": 7,
    "level_weight": 0.7,
    "slope_weight": 0.3,
    "flat_range_epsilon": 0.5,
}


@dataclass(frozen=True, slots=True)
class ValenceVectorizationProfile:
    """All settings that determine persisted emotional-arc vector semantics."""

    algorithm: str
    profile_revision: int
    value_min: float
    value_max: float
    dimensions: int
    canonical_points: int
    interpolation: str
    dct_type: int
    dct_norm: str
    slope_method: str
    level_dimensions: int
    slope_dimensions: int
    level_weight: float
    slope_weight: float
    flat_range_epsilon: float

    def fingerprint(self) -> str:
        """Return a stable fingerprint for the complete vector semantics."""
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ValenceVectorizationError(ValueError):
    """Valence input or vector math violated the configured profile."""


def load_valence_vectorization_profile(raw: object) -> ValenceVectorizationProfile:
    """Load the sole supported valence-vectorization profile without defaults."""
    if not isinstance(raw, Mapping):
        raise ValenceVectorizationError("valence_vectorization must be a mapping")

    expected_keys = {
        "algorithm",
        "profile_revision",
        "value_min",
        "value_max",
        "dimensions",
        "canonical_points",
        "interpolation",
        "dct_type",
        "dct_norm",
        "slope_method",
        "level_dimensions",
        "slope_dimensions",
        "level_weight",
        "slope_weight",
        "flat_range_epsilon",
    }
    actual_keys = set(raw)
    if missing_keys := expected_keys - actual_keys:
        raise ValenceVectorizationError(f"valence_vectorization is missing keys: {sorted(missing_keys)}")
    if unknown_keys := actual_keys - expected_keys:
        raise ValenceVectorizationError(f"valence_vectorization has unknown keys: {sorted(unknown_keys)}")

    for field_name, supported_value in SUPPORTED_PROFILE_VALUES.items():
        value = raw[field_name]
        if isinstance(supported_value, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValenceVectorizationError(f"{field_name} must be an integer")
        elif not isinstance(value, str):
            raise ValenceVectorizationError(f"{field_name} must be a string")
        if value != supported_value:
            raise ValenceVectorizationError(f"unsupported {field_name}: {value!r}")

    value_min = _finite_number(raw["value_min"], "value_min")
    value_max = _finite_number(raw["value_max"], "value_max")
    level_weight = _finite_number(raw["level_weight"], "level_weight")
    slope_weight = _finite_number(raw["slope_weight"], "slope_weight")
    flat_range_epsilon = _finite_number(raw["flat_range_epsilon"], "flat_range_epsilon")
    if value_min >= value_max:
        raise ValenceVectorizationError("value_min must be less than value_max")
    if flat_range_epsilon <= 0.0:
        raise ValenceVectorizationError("flat_range_epsilon must be positive")
    if level_weight <= 0.0 or slope_weight <= 0.0:
        raise ValenceVectorizationError("level_weight and slope_weight must be positive")
    if not math.isclose(level_weight + slope_weight, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValenceVectorizationError("level_weight and slope_weight must sum to 1.0")

    dimensions = SUPPORTED_PROFILE_VALUES["dimensions"]
    level_dimensions = SUPPORTED_PROFILE_VALUES["level_dimensions"]
    slope_dimensions = SUPPORTED_PROFILE_VALUES["slope_dimensions"]
    canonical_points = SUPPORTED_PROFILE_VALUES["canonical_points"]
    if level_dimensions + slope_dimensions + 1 != dimensions:
        raise ValenceVectorizationError("component dimensions must match dimensions")
    if canonical_points < level_dimensions or canonical_points - 1 < slope_dimensions:
        raise ValenceVectorizationError("canonical_points does not satisfy DCT coefficient requirements")

    return ValenceVectorizationProfile(
        algorithm=SUPPORTED_PROFILE_VALUES["algorithm"],
        profile_revision=SUPPORTED_PROFILE_VALUES["profile_revision"],
        value_min=value_min,
        value_max=value_max,
        dimensions=dimensions,
        canonical_points=canonical_points,
        interpolation=SUPPORTED_PROFILE_VALUES["interpolation"],
        dct_type=SUPPORTED_PROFILE_VALUES["dct_type"],
        dct_norm=SUPPORTED_PROFILE_VALUES["dct_norm"],
        slope_method=SUPPORTED_PROFILE_VALUES["slope_method"],
        level_dimensions=level_dimensions,
        slope_dimensions=slope_dimensions,
        level_weight=level_weight,
        slope_weight=slope_weight,
        flat_range_epsilon=flat_range_epsilon,
    )


class ValenceVectorizer:
    """Vectorize valence curves under one validated, explicit profile."""

    def __init__(self, profile: ValenceVectorizationProfile) -> None:
        self.profile = profile

    def vectorize(self, valence_values: Sequence[float]) -> list[float]:
        """Return a normalized shape, slope, and flatness vector for a Valence curve."""
        values = self._validate_valence_values(valence_values)
        if float(np.max(values) - np.min(values)) <= self.profile.flat_range_epsilon:
            return [0.0] * (self.profile.dimensions - 1) + [1.0]

        level = self._resample(values)
        level_std = float(np.std(level))
        if not math.isfinite(level_std) or level_std <= 0.0:
            raise ValenceVectorizationError("non-flat Valence curve produced a zero level standard deviation")
        level = (level - float(np.mean(level))) / level_std
        if not bool(np.isfinite(level).all()):
            raise ValenceVectorizationError("resampled level contains non-finite values")

        level_coefficients = dct(level, type=self.profile.dct_type, norm=self.profile.dct_norm)[
            1 : 1 + self.profile.level_dimensions
        ]
        shape_part = self._weighted_unit_component(
            level_coefficients, self.profile.level_dimensions, self.profile.level_weight, "shape"
        )
        slope_coefficients = dct(np.diff(level), type=self.profile.dct_type, norm=self.profile.dct_norm)[
            : self.profile.slope_dimensions
        ]
        slope_part = self._weighted_unit_component(
            slope_coefficients, self.profile.slope_dimensions, self.profile.slope_weight, "slope"
        )
        vector = np.concatenate((shape_part, slope_part, np.asarray([0.0])))
        if vector.size != self.profile.dimensions:
            raise ValenceVectorizationError("generated vector has an unexpected dimension")
        vector_norm = float(np.linalg.norm(vector))
        if not math.isfinite(vector_norm) or vector_norm <= 0.0:
            raise ValenceVectorizationError("non-flat Valence curve produced a zero vector")
        vector = vector / vector_norm
        if not bool(np.isfinite(vector).all()):
            raise ValenceVectorizationError("generated vector contains non-finite values")
        return [float(value) for value in vector]

    def cosine_similarity(self, left: Sequence[float], right: Sequence[float]) -> float:
        """Compute cosine similarity after rejecting vectors outside this profile."""
        left_array = self._validate_vector(left, "left")
        right_array = self._validate_vector(right, "right")
        left_norm = float(np.linalg.norm(left_array))
        right_norm = float(np.linalg.norm(right_array))
        if left_norm <= 0.0 or right_norm <= 0.0:
            raise ValenceVectorizationError("cosine similarity requires non-zero vectors")
        similarity = float(np.dot(left_array, right_array) / (left_norm * right_norm))
        if not math.isfinite(similarity):
            raise ValenceVectorizationError("cosine similarity is not finite")
        return similarity

    def _validate_valence_values(self, valence_values: Sequence[float]) -> np.ndarray:
        if isinstance(valence_values, (str, bytes)):
            raise ValenceVectorizationError("valence_values must be a numeric sequence")
        try:
            raw_values = list(valence_values)
        except TypeError as error:
            raise ValenceVectorizationError("valence_values must be a numeric sequence") from error
        if not raw_values:
            raise ValenceVectorizationError("valence_values must not be empty")
        values = np.asarray([_finite_number(value, "valence value") for value in raw_values], dtype=float)
        if bool(np.any(values < self.profile.value_min)) or bool(np.any(values > self.profile.value_max)):
            raise ValenceVectorizationError(
                f"valence values must be within {self.profile.value_min}..{self.profile.value_max}"
            )
        return values

    def _resample(self, values: np.ndarray) -> np.ndarray:
        source_time = np.linspace(0.0, 1.0, values.size)
        canonical_time = np.linspace(0.0, 1.0, self.profile.canonical_points)
        if values.size == 1:
            return np.full(self.profile.canonical_points, values[0], dtype=float)
        if values.size == 2:
            return np.interp(canonical_time, source_time, values)
        try:
            return np.asarray(PchipInterpolator(source_time, values)(canonical_time), dtype=float)
        except (TypeError, ValueError) as error:
            raise ValenceVectorizationError("PCHIP interpolation failed") from error

    @staticmethod
    def _weighted_unit_component(
        coefficients: np.ndarray, expected_dimensions: int, weight: float, component_name: str
    ) -> np.ndarray:
        if coefficients.size != expected_dimensions:
            raise ValenceVectorizationError(f"{component_name} component has an unexpected dimension")
        norm = float(np.linalg.norm(coefficients))
        if not math.isfinite(norm):
            raise ValenceVectorizationError(f"{component_name} component has a non-finite norm")
        if norm == 0.0:
            return np.zeros(expected_dimensions, dtype=float)
        return coefficients / norm * math.sqrt(weight)

    def _validate_vector(self, values: Sequence[float], label: str) -> np.ndarray:
        if isinstance(values, (str, bytes)):
            raise ValenceVectorizationError(f"{label} vector must be numeric")
        try:
            vector = np.asarray([_finite_number(value, f"{label} vector value") for value in values], dtype=float)
        except TypeError as error:
            raise ValenceVectorizationError(f"{label} vector must be a numeric sequence") from error
        if vector.size != self.profile.dimensions:
            raise ValenceVectorizationError(f"{label} vector must have {self.profile.dimensions} dimensions")
        return vector


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValenceVectorizationError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValenceVectorizationError(f"{field_name} must be a finite number")
    return number


default_valence_vectorizer = ValenceVectorizer(load_valence_vectorization_profile(DEFAULT_PROFILE_RAW))
