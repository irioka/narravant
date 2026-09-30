"""Compatibility filename for the current Valence-vector regression contract."""

from narravant.core.valence_vector import default_valence_vectorizer


def test_rising_shape_is_more_similar_to_rising_than_to_falling() -> None:
    rising = default_valence_vectorizer.vectorize([1, 3, 5, 7])
    another_rising = default_valence_vectorizer.vectorize([1, 2, 4, 7])
    falling = default_valence_vectorizer.vectorize([7, 5, 3, 1])

    assert default_valence_vectorizer.cosine_similarity(rising, another_rising) > 0.85
    assert default_valence_vectorizer.cosine_similarity(rising, falling) < 0.0
