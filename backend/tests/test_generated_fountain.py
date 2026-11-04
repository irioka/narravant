from __future__ import annotations

import pytest

from narravant.core.generated_fountain import (
    GeneratedFountainCueError,
    strip_unknown_generated_fountain_tags,
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


def test_generated_notation_rejects_unclosed_reaction_at_source_line() -> None:
    fountain = "INT. ROOM - DAY #1#\n\n@MAYA\n[[listener:JON]]\nHello |unfinished"

    with pytest.raises(GeneratedFountainCueError) as caught:
        validate_generated_fountain_cues(
            fountain,
            known_source_speakers=["MAYA", "JON"],
            strict_speaker_names=True,
        )

    assert caught.value.reason == "unclosed_pipe"
    assert caught.value.line_number == 5
    assert caught.value.scene_number == 1


def test_generated_notation_rejects_unknown_tags_without_listener_note() -> None:
    fountain = "INT. ROOM - DAY #1#\n\n@MAYA\nHello <unknown>."

    with pytest.raises(GeneratedFountainCueError) as caught:
        validate_generated_fountain_cues(fountain, known_source_speakers=["MAYA"], strict_speaker_names=True)

    assert caught.value.reason == "unknown_tag"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_generated_tag_cleanup_preserves_structure_notes_styles_and_allowed_tags(newline) -> None:
    source = newline.join([
        "Title: Synthetic <metadata>",
        "",
        "INT. <location> - DAY #6#",
        "",
        "[[Private note:",
        "<note>Keep this note unchanged.",
        "]]",
        "# Outline <outline>",
        "",
        "@MAYA",
        "(quietly <direction>)",
        "[[listener:JON]]",
        "  <unsupported>Hello <breath> |<sigh>Yes.</unknown>|  ",
        "  <short pause>We should leave.<unsupported>  ",
        "",
        "@JON",
        "Let's go.",
        "",
        "@Narrator",
        "They leave <legacy>together.",
        "",
    ])
    expected = source.replace("<unsupported>", "").replace("</unknown>", "").replace("<legacy>", "")
    cleaned, count = strip_unknown_generated_fountain_tags(source)

    assert cleaned == expected
    assert count == 4
    validate_generated_fountain_cues(
        cleaned, known_source_speakers=["MAYA", "JON"], strict_speaker_names=True
    )
    assert strip_unknown_generated_fountain_tags(cleaned) == (cleaned, 0)


@pytest.mark.parametrize("text", ["<unfinished", "<<nested>>"])
def test_generated_tag_cleanup_does_not_accept_malformed_notation(text) -> None:
    source = f"INT. ROOM - DAY #1#\n\n@MAYA\nHello {text}."
    with pytest.raises(GeneratedFountainCueError):
        cleaned, _count = strip_unknown_generated_fountain_tags(source)
        validate_generated_fountain_cues(cleaned, known_source_speakers=["MAYA"], strict_speaker_names=True)


@pytest.mark.parametrize(
    "exposed_structure",
    ["@JON", "JON", "(quietly)", "[[note]]", "# Outline", "EXT. ROAD - DAY"],
)
def test_generated_tag_cleanup_rejects_promoting_speech_to_structure(exposed_structure) -> None:
    source = f"INT. ROOM - DAY #1#\n\n@MAYA\nHello.\n<unknown>{exposed_structure}\nLet's go."
    with pytest.raises(GeneratedFountainCueError, match="^tag_removal_changes_structure$"):
        strip_unknown_generated_fountain_tags(source)


@pytest.mark.parametrize(
    "body",
    ["Hello.\n<unknown>", "<unknown>\nHello.", "<unknown>\nHello.\n<unknown>",
     "[[listener:JON]]\nHello |Yes| We should go.\n<unknown>"],
)
def test_generated_tag_cleanup_accepts_edge_tag_lines_without_changing_speech(body) -> None:
    source = "INT. ROOM - DAY #4#\n\n@MAYA\n" + body
    cleaned, removed = strip_unknown_generated_fountain_tags(source)
    assert cleaned == source.replace("<unknown>", "")
    assert removed == body.count("<unknown>")
    validate_generated_fountain_cues(cleaned, known_source_speakers=["MAYA", "JON"], strict_speaker_names=True)


def test_generated_tag_cleanup_still_rejects_a_middle_line_changing_speaker() -> None:
    source = "INT. ROOM - DAY #4#\n\n@MAYA\nHello.\n<unknown>\nWe should go."
    with pytest.raises(GeneratedFountainCueError, match="^tag_removal_changes_structure$"):
        strip_unknown_generated_fountain_tags(source)


@pytest.mark.parametrize("tags", ["<breath>", "<short pause>", "<breath> <sigh>"])
def test_generated_notation_rejects_tag_only_utterance_before_analysis(tags: str) -> None:
    fountain = "INT. ROOM - DAY #1#\n\n@MAYA\n" + tags

    with pytest.raises(GeneratedFountainCueError) as caught:
        validate_generated_fountain_cues(fountain, known_source_speakers=["MAYA"], strict_speaker_names=True)

    assert caught.value.reason == "missing_speech"
    assert caught.value.scene_number == 1


def test_generated_notation_accepts_vocal_tag_with_spoken_body() -> None:
    validate_generated_fountain_cues("INT. ROOM - DAY #1#\n\n@MAYA\n<breath> I can keep walking.")

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
