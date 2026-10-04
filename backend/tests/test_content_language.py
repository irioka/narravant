from __future__ import annotations

import pytest

from narravant.core.content_language import (
    ContentLanguageMismatch,
    infer_source_language,
    source_language_instruction,
    validate_text_language,
)

EN = "The river rose, and she knew that her friend must find shelter with the group."
JA = "川の水が増えたため、少女は友人と一緒に安全な小屋を探しました。二人は協力しながら出口を見つけました。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (EN, "en"),
        (JA, "ja"),
        ("MAYA", "und"),
        ("山河天地", "und"),
        ("El río creció mientras una joven buscaba un refugio seguro para su amiga.", "und"),
        (EN + " 桜", "en"),
    ],
)
def test_source_language_is_conservative(text: str, expected: str) -> None:
    assert infer_source_language(text) == expected


def test_japanese_output_for_english_source_is_rejected() -> None:
    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        validate_text_language(JA, "en")
    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        validate_text_language("川の水が増水し、少女は小屋へ逃げ込んだ。", "en")
    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        validate_text_language("危険な嵐から逃れる少女の物語。", "en")
    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        validate_text_language("家に戻りたい。", "en")
    with pytest.raises(ContentLanguageMismatch, match="^output_language$"):
        validate_text_language("Save the town from the flood.", "ja")
    validate_text_language(EN + " 桜", "en")
    validate_text_language("MAYA", "en")
    validate_text_language("AliceとBobは協力した。", "ja")
    validate_text_language(JA, "und")


def test_source_language_instruction() -> None:
    en_inst = source_language_instruction("en")
    assert "English" in en_inst
    ja_inst = source_language_instruction("ja")
    assert "Japanese" in ja_inst
    und_inst = source_language_instruction("und")
    assert "und" not in und_inst


@pytest.mark.parametrize("text", ["MAYA", "ドロシー", "山河天地", "El río creció mientras una joven buscaba refugio."])
def test_short_names_and_unsupported_language_are_not_rejected(text):
    validate_text_language(text, "en")
    validate_text_language(text, "ja")
