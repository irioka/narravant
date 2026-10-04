from __future__ import annotations

import pytest

from narravant.core.generated_fountain import (
    GeneratedFountainCueError,
    validate_generated_fountain_cues,
)


@pytest.mark.parametrize("suffix", ["", "\n@JON\nHello.", "\nEXT. ROAD - DAY #8#"])
def test_empty_cue_reports_safe_position(suffix: str) -> None:
    with pytest.raises(GeneratedFountainCueError) as caught:
        validate_generated_fountain_cues("INT. ROOM - DAY #7#\n\n@MAYA\n(quietly)" + suffix)
    assert caught.value.reason == "cue_without_speech"
    assert caught.value.line_number == 3
    assert caught.value.scene_number == 7
    assert str(caught.value) == "cue_without_speech"
    assert "MAYA" not in repr(vars(caught.value))


@pytest.mark.parametrize(
    "body",
    [
        "@MAYA stands beside @JON, watching the rain.\nThe shutter rattles.",
        "@MAYA sits on the wooden floor.\nThe shutter rattles.",
        "@MAYA\n(quietly)\n\n@JON\nHello.",
    ],
)
def test_invalid_generated_cue_is_rejected(body: str) -> None:
    with pytest.raises(GeneratedFountainCueError):
        validate_generated_fountain_cues(
            "INT. CABIN - DAY #1#\n\n" + body,
            known_source_speakers=["MAYA", "JON"],
            strict_speaker_names=True,
        )


def test_valid_cues_with_narrator_and_contd_are_accepted() -> None:
    valid_fountain = (
        "Title: Storm Cabin\n\n"
        "INT. CABIN - DAY #1#\n\n"
        "@Narrator\n"
        "(calmly)\n"
        "Maya sits on the floor.\n\n"
        "@MAYA\n"
        "(quietly)\n"
        "I can hear the rain.\n\n"
        "@MAYA (CONT'D)\n"
        "(whispering)\n"
        "It is getting louder.\n"
    )
    validate_generated_fountain_cues(
        valid_fountain,
        known_source_speakers=["MAYA"],
        strict_speaker_names=True,
    )


def test_mixed_case_and_dotted_names_are_preserved() -> None:
    valid_fountain = (
        "INT. CABIN - DAY #1#\n\n"
        "@McClane\n"
        "Hold on.\n\n"
        "@M. de la Cruz\n"
        "We need help.\n\n"
        "@ナレーター\n"
        "静けさが戻る。\n"
    )
    validate_generated_fountain_cues(
        valid_fountain,
        known_source_speakers=["McClane", "M. de la Cruz"],
        strict_speaker_names=True,
    )


def test_sentence_in_cue_with_known_source_speaker_is_exempt() -> None:
    # "MAYA van Meer." matches the sentence-in-cue pattern, but should be allowed if in known_source_speakers
    fountain = "INT. CABIN - DAY #1#\n\n@MAYA van Meer.\nI am here.\n"
    validate_generated_fountain_cues(
        fountain,
        known_source_speakers=["MAYA van Meer."],
        strict_speaker_names=False,
    )
    # If not known and not strict, sentence_in_cue triggers
    with pytest.raises(GeneratedFountainCueError, match="^sentence_in_cue$"):
        validate_generated_fountain_cues(
            fountain,
            known_source_speakers=[],
            strict_speaker_names=False,
        )
