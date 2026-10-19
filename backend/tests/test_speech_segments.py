from __future__ import annotations

import pytest

from narravant.core.speech_notation import parse_speech_notation
from narravant.services.playback import PlannedUtterance
from narravant.services.speech_segments import (
    SegmentVoice,
    SpeechSegment,
    SpeechSegmentError,
    build_speech_segments,
)
from narravant.services.tts import TtsError, build_tts_request


def _planned_utterance(text: str, listener: str | None = None) -> PlannedUtterance:
    listener_voice = SegmentVoice(listener, "voice_test_listener") if listener else None
    return PlannedUtterance(
        scene_number=3,
        utterance_index=2,
        speaker="旅人",
        target_type="character",
        text=text,
        voice_id="voice_test_primary",
        performance_direction="不安を隠して問いかける",
        notation=parse_speech_notation(text, listener),
        listener_voice=listener_voice,
    )


def test_sentence_segments_keep_inline_pause_and_two_speaker_reaction() -> None:
    utterance = _planned_utterance("この道で |うん| 間違いない？<short pause>次の角を左へ。", "案内人")

    segments = build_speech_segments(utterance)

    assert [segment.text for segment in segments] == [
        "この道で |うん| 間違いない？<short pause>",
        "次の角を左へ。",
    ]
    assert segments[0].primary == SegmentVoice("旅人", "voice_test_primary")
    assert segments[0].listener == SegmentVoice("案内人", "voice_test_listener")
    assert segments[1].listener is None
    assert all(segment.style == "不安を隠して問いかける" for segment in segments)


def test_boundary_pause_stays_with_one_adjacent_text_segment() -> None:
    utterance = _planned_utterance("<short pause>始まる。終わり。<long pause>")

    segments = build_speech_segments(utterance)

    assert [segment.text for segment in segments] == ["<short pause>始まる。", "終わり。<long pause>"]
    assert sum(segment.text.count("<short pause>") for segment in segments) == 1
    assert sum(segment.text.count("<long pause>") for segment in segments) == 1


def test_abbreviations_and_decimal_points_do_not_split_sentences() -> None:
    utterance = _planned_utterance("Dr. Gray measured 3.14. Then left.")

    assert [segment.text for segment in build_speech_segments(utterance)] == [
        "Dr. Gray measured 3.14.",
        "Then left.",
    ]


def test_long_reaction_fails_with_source_position() -> None:
    utterance = _planned_utterance(f"返事を待つ |{'あ' * 181}|", "案内人")

    with pytest.raises(SpeechSegmentError) as exc_info:
        build_speech_segments(utterance)

    assert exc_info.value.reason == "reaction_too_long"
    assert exc_info.value.scene_number == 3
    assert exc_info.value.utterance_index == 2
    assert exc_info.value.offset == len("返事を待つ ")


def test_overlap_provider_request_is_marked_unsupported() -> None:
    segment = SpeechSegment(
        text="この道で |うん| 間違いない？",
        primary=SegmentVoice("旅人", "voice_test_primary"),
        listener=SegmentVoice("案内人", "voice_test_listener"),
        style="不安を隠して問いかける",
    )

    with pytest.raises(TtsError, match="Gemini TTS では.*二話者.*未対応"):
        build_tts_request(segment, "gemini-3.8-flash-tts", "ja-JP")
