"""Unit tests for align_character_arcs."""

from narravant.services.ingestion import align_character_arcs


def test_align_character_arcs_exact_match() -> None:
    generated = {"Alice": [1, 2, 3], "Bob": [4, 5, 6]}
    expected = ["Alice", "Bob"]
    result = align_character_arcs(generated, expected, 3)
    assert result == {"Alice": [1, 2, 3], "Bob": [4, 5, 6]}


def test_align_character_arcs_parenthesized_aliases() -> None:
    # LLM returned shortened name
    generated = {"茨城暦": [4, 5, 4]}
    expected = ["茨城暦（宇賀貞治）"]
    result = align_character_arcs(generated, expected, 3)
    assert result == {"茨城暦（宇賀貞治）": [4, 5, 4]}

    # LLM returned full name with parentheses, expected is base name
    generated = {"茨城暦(宇賀貞治)": [4, 5, 4]}
    expected = ["茨城暦"]
    result = align_character_arcs(generated, expected, 3)
    assert result == {"茨城暦": [4, 5, 4]}


def test_align_character_arcs_missing_and_unexpected_characters() -> None:
    generated = {
        "茨城暦": [4, 5, 4],
        "ExtraPerson": [1, 2, 3],
    }
    expected = ["茨城暦（宇賀貞治）", "秋山"]
    result = align_character_arcs(generated, expected, 3)
    assert "ExtraPerson" not in result
    assert result["茨城暦（宇賀貞治）"] == [4, 5, 4]
    # Missing character filled with 0 (absence)
    assert result["秋山"] == [0, 0, 0]


def test_align_character_arcs_invalid_scene_length() -> None:
    generated = {
        "Alice": [4, 5],  # length 2, but scene_count is 3
    }
    expected = ["Alice"]
    result = align_character_arcs(generated, expected, 3)
    assert result == {"Alice": [0, 0, 0]}
