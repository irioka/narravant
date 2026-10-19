import pytest

from narravant.core.speech_notation import (
    SpeechNotationError,
    parse_listener_note,
    parse_speech_notation,
    spoken_text_by_speaker,
    spoken_turns,
)


def test_reaction_belongs_to_listener_and_pause_is_not_spoken() -> None:
    listener = parse_listener_note("[[listener:案内人]]")
    notation_text = "この道で |うん| 間違いない？<short pause>"
    notation = parse_speech_notation(notation_text, listener)
    lines = spoken_text_by_speaker("旅人", notation)

    assert lines["旅人"].replace(" ", "") == "この道で間違いない？"
    assert lines["案内人"] == "うん"
    assert spoken_turns("旅人", notation) == (("旅人", "この道で "), ("案内人", "うん"), ("旅人", " 間違いない？"))
    pause = next(token for token in notation.tokens if token.kind == "pause")
    assert notation_text[pause.start : pause.end] == "<short pause>"


def test_empty_listener_is_rejected_with_reason_and_offset() -> None:
    with pytest.raises(SpeechNotationError) as caught:
        parse_listener_note("[[listener:   ]]")

    assert caught.value.reason == "empty_listener"
    assert caught.value.offset is not None


def test_plain_pipe_and_unknown_tag_remain_literal_without_listener_activation() -> None:
    notation = parse_speech_notation("literal |pipe| and <legacy-marker>")

    assert spoken_text_by_speaker("話者", notation) == {"話者": "literal |pipe| and <legacy-marker>"}


@pytest.mark.parametrize("text", ["main |open", "main |outer |nested||"])
def test_invalid_pipe_notation_is_rejected_when_listener_is_explicit(text: str) -> None:
    with pytest.raises(SpeechNotationError):
        parse_speech_notation(text, "相手")


def test_unknown_tag_is_rejected_when_listener_activates_new_notation() -> None:
    with pytest.raises(SpeechNotationError) as caught:
        parse_speech_notation("main <unregistered> |reaction|", "相手")

    assert caught.value.reason == "unknown_tag"
    assert caught.value.offset == 5
