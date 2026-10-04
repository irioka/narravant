from __future__ import annotations

import re
from typing import Literal

SourceLanguage = Literal["en", "ja", "und"]

_ENGLISH_AUXILIARY_WORDS = frozenset({
    "the",
    "and",
    "was",
    "were",
    "with",
    "that",
    "this",
    "she",
    "his",
    "her",
    "they",
    "their",
})

_KANA_PATTERN = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
_CJK_PATTERN = re.compile(r"[\u4E00-\u9FFF]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_WORD_PATTERN = re.compile(r"[a-z]+")


class ContentLanguageMismatch(ValueError):
    """Raised when generated text clearly contradicts the source language."""

    def __init__(self, message: str = "output_language") -> None:
        super().__init__("output_language")


def infer_source_language(text: str) -> SourceLanguage:
    """Conservatively infer source language as 'en', 'ja', or 'und'.

    Symbols, numbers, and JSON keys are not treated as evidence of language.
    Short text, mixed text, and unsupported languages fall back to 'und'.
    """
    if not text:
        return "und"

    kana_count = len(_KANA_PATTERN.findall(text))
    cjk_count = len(_CJK_PATTERN.findall(text))
    latin_count = len(_LATIN_PATTERN.findall(text))

    kana_and_cjk = kana_count + cjk_count

    # Japanese check: at least 10 kana, and kana+CJK at least 3x Latin letters
    if kana_count >= 10 and kana_and_cjk >= latin_count * 3:
        return "ja"

    # English check: at least 40 Latin letters, Latin letters at least 3x kana+CJK,
    # and at least 4 distinct English auxiliary words.
    if latin_count >= 40 and latin_count >= kana_and_cjk * 3:
        words = set(_WORD_PATTERN.findall(text.lower()))
        matched_aux = words & _ENGLISH_AUXILIARY_WORDS
        if len(matched_aux) >= 4:
            return "en"

    return "und"


def validate_text_language(text: str, expected: SourceLanguage) -> None:
    """Validate that text does not clearly contradict expected language.

    原稿推定の閾値に加え、生成説明の短い文にも限定的な検査を行う。
    固有名だけの短文・混在・対象外の言語を一律に拒否しない。
    """
    if expected not in ("en", "ja"):
        return

    inferred = infer_source_language(text)
    if (expected == "en" and inferred == "ja") or (expected == "ja" and inferred == "en"):
        raise ContentLanguageMismatch()

    # 原稿全体の推定閾値を緩めず、句点付きの説明文だけを補足する。
    kana = len(_KANA_PATTERN.findall(text))
    japanese = kana + len(_CJK_PATTERN.findall(text))
    latin = len(_LATIN_PATTERN.findall(text))
    if expected == "en" and kana >= 3 and japanese >= 3 * latin and re.search(r"[。！？.!?]", text):
        raise ContentLanguageMismatch()
    words = _WORD_PATTERN.findall(text.lower())
    if (
        expected == "ja"
        and latin >= 20
        and latin >= 3 * japanese
        and len(words) >= 5
        and set(words) & _ENGLISH_AUXILIARY_WORDS
        and re.search(r"[.!?]", text)
    ):
        raise ContentLanguageMismatch()


def source_language_instruction(language: SourceLanguage) -> str:
    """Return prompt instruction for source language preservation."""
    if language == "en":
        return (
            "SOURCE LANGUAGE: English. OUTPUT LANGUAGE: English.\n"
            "Write synopsis, theme, character profiles, voice traits, turning points (change and reason), "
            "and all spoken dialogue in English. Do NOT translate to Japanese.\n"
            "Keep supplied character names, proper nouns, JSON keys, and enums untranslated."
        )
    if language == "ja":
        return (
            "SOURCE LANGUAGE: Japanese. OUTPUT LANGUAGE: Japanese.\n"
            "あらすじ・テーマ・人物プロフィール・声の特性・転換点（change / reason）・"
            "台詞・地の文をすべて日本語で記述してください。英語へ翻訳しないでください。\n"
            "指定された登場人物名・固有名詞・JSONキー・enum は変更しないでください。"
        )
    return (
        "Preserve the primary language of the source document for all natural language fields "
        "(synopsis, theme, character profiles, turning points, and dialogue). Do NOT translate to another language.\n"
        "Keep supplied character names, proper nouns, JSON keys, and enums untranslated."
    )
