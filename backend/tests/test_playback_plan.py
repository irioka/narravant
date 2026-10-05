from __future__ import annotations

import pytest

from narravant.services.playback import (
    PlaybackPlanError,
    build_playback_plan,
)

SAMPLE_FOUNTAIN = """Title: Audiobook Test

INT. STUDY - NIGHT #1#

The room is dark and silent.

@ALICE
Is someone there?

The grandfather clock ticks.

@BOB
It is only me, Alice.

INT. GARDEN - MORNING #2#

Morning sunlight fills the yard.

@ALICE
We made it through the night.
"""


@pytest.mark.parametrize(
    ("source", "assigned_name"),
    [
        ("INT. ROOM - DAY #1#\n\nThe room is quiet.", "ナレーター"),
        ("Title: 日本語のタイトル\n\nINT. ROOM - DAY #1#\n\n@Narrator\nHello.", "ナレーター"),
        ("INT. ROOM - DAY #1#\n\n@NARRATOR\nHello.", "Narrator"),
        ("INT. ROOM - DAY #1#\n\n@ナレーター\nこんにちは。", "Narrator"),
    ],
)
def test_narrator_voice_assignments_remain_compatible_across_languages(source, assigned_name):
    plan = build_playback_plan(
        {
            "source_fountain": source,
            "voice_assignments": [{"speaker": assigned_name, "voice_id": "voices/synthetic-narrator"}],
        }
    )
    assert plan.utterances[0].target_type == "narrator"
    assert plan.utterances[0].voice_id == "voices/synthetic-narrator"


def test_playback_narrates_scene_heading_without_classification_or_number():
    source_fountain = """Title: Scene heading narration

INT./EXT. HIGHWAY 66 - DAWN #12#

A car appears.

@MAYA
Stop!

.OLD RUINS #13#

Dust rises.
"""
    plan = build_playback_plan(
        {
            "source_fountain": source_fountain,
            "voice_assignments": [
                {"speaker": "Narrator", "voice_id": "voices/synthetic-narrator"},
                {"speaker": "MAYA", "voice_id": "voices/synthetic-maya"},
            ],
        }
    )

    assert [
        (item.scene_number, item.utterance_index, item.speaker, item.target_type, item.text)
        for item in plan.utterances
    ] == [
        (12, 0, "Narrator", "narrator", "HIGHWAY 66 - DAWN"),
        (12, 1, "Narrator", "narrator", "A car appears."),
        (12, 2, "MAYA", "character", "Stop!"),
        (13, 0, "Narrator", "narrator", "OLD RUINS"),
        (13, 1, "Narrator", "narrator", "Dust rises."),
    ]
    assert all(
        marker not in item.text
        for item in plan.utterances
        for marker in ("INT./EXT.", "#12#", "#13#")
    )


@pytest.mark.parametrize(
    ("classification", "location"),
    [
        ("INT.", "ROOM"),
        ("EXT.", "ROAD"),
        ("EST.", "CITY"),
        ("INT/EXT", "TRAIN"),
        ("I/E.", "SHIP"),
    ],
)
def test_playback_strips_each_scene_heading_classification(classification, location):
    plan = build_playback_plan(
        {
            "source_fountain": f"Title: Heading only\n\n{classification} {location} #7#\n",
            "voice_assignments": [{"speaker": "Narrator", "voice_id": "voices/synthetic-narrator"}],
        }
    )

    assert [(item.scene_number, item.utterance_index, item.text) for item in plan.utterances] == [(7, 0, location)]


def test_build_playback_plan_success_ordering_and_speaker_types():
    """Verify playback plan establishes correct sequence, scene+index IDs, and voice assignments."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voices/fake-narrator-1"},
            {"speaker": "ALICE", "voice_id": "voices/fake-alice-2"},
            {"speaker": "BOB", "voice_id": "voices/fake-bob-3"},
        ],
    }

    plan = build_playback_plan(document_data)
    assert plan.document_id == "doc-test-1"
    assert plan.version_id == 1
    assert plan.total_utterances == 8
    assert len(plan.utterances) == 8

    # Scene 1, Utterance 0: Spoken scene heading
    u0 = plan.utterances[0]
    assert u0.scene_number == 1
    assert u0.utterance_index == 0
    assert u0.speaker == "Narrator"
    assert u0.target_type == "narrator"
    assert u0.text == "STUDY - NIGHT"
    assert u0.voice_id == "voices/fake-narrator-1"

    # Scene 1, Utterance 1: Narration
    u1 = plan.utterances[1]
    assert u1.scene_number == 1
    assert u1.utterance_index == 1
    assert u1.speaker == "Narrator"
    assert u1.target_type == "narrator"
    assert u1.text == "The room is dark and silent."
    assert u1.voice_id == "voices/fake-narrator-1"

    # Scene 1, Utterance 2: Alice dialogue
    u2 = plan.utterances[2]
    assert u2.scene_number == 1
    assert u2.utterance_index == 2
    assert u2.speaker == "ALICE"
    assert u2.target_type == "character"
    assert u2.text == "Is someone there?"
    assert u2.voice_id == "voices/fake-alice-2"

    # Scene 1, Utterance 3: Narration
    u3 = plan.utterances[3]
    assert u3.scene_number == 1
    assert u3.utterance_index == 3
    assert u3.speaker == "Narrator"
    assert u3.target_type == "narrator"
    assert u3.text == "The grandfather clock ticks."
    assert u3.voice_id == "voices/fake-narrator-1"

    # Scene 1, Utterance 4: Bob dialogue
    u4 = plan.utterances[4]
    assert u4.scene_number == 1
    assert u4.utterance_index == 4
    assert u4.speaker == "BOB"
    assert u4.target_type == "character"
    assert u4.voice_id == "voices/fake-bob-3"

    # Scene 2, Utterance 0: Spoken scene heading
    u5 = plan.utterances[5]
    assert u5.scene_number == 2
    assert u5.utterance_index == 0
    assert u5.speaker == "Narrator"
    assert u5.text == "GARDEN - MORNING"

    # Scene 2, Utterance 1: Narration
    u6 = plan.utterances[6]
    assert u6.scene_number == 2
    assert u6.utterance_index == 1
    assert u6.speaker == "Narrator"
    assert u6.text == "Morning sunlight fills the yard."

    # Scene 2, Utterance 2: Alice dialogue
    u7 = plan.utterances[7]
    assert u7.scene_number == 2
    assert u7.utterance_index == 2
    assert u7.speaker == "ALICE"
    assert u7.target_type == "character"
    assert u7.text == "We made it through the night."


def test_build_playback_plan_with_start_position():
    """Verify plan can filter from a specific start scene and utterance index (for resume/retry)."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voices/fake-narrator-1"},
            {"speaker": "ALICE", "voice_id": "voices/fake-alice-2"},
            {"speaker": "BOB", "voice_id": "voices/fake-bob-3"},
        ],
    }

    # Start from Scene 1, Utterance 3 (the grandfather clock narration onwards)
    plan = build_playback_plan(document_data, start_scene=1, start_utterance_index=3)
    assert plan.total_utterances == 8
    assert len(plan.utterances) == 5
    assert plan.utterances[0].scene_number == 1
    assert plan.utterances[0].utterance_index == 3

    # Start from Scene 2, Utterance 0
    plan_s2 = build_playback_plan(document_data, start_scene=2, start_utterance_index=0)
    assert len(plan_s2.utterances) == 3
    assert plan_s2.utterances[0].scene_number == 2
    assert plan_s2.utterances[0].utterance_index == 0


def test_build_playback_plan_unassigned_voice_detection():
    """R4.5, R4.6: If any speaker has no assigned voice_id, detect and halt before playback."""
    # BOB has None voice_id
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voices/fake-narrator-1"},
            {"speaker": "ALICE", "voice_id": "voices/fake-alice-2"},
            {"speaker": "BOB", "voice_id": None},
        ],
    }

    with pytest.raises(PlaybackPlanError) as exc_info:
        build_playback_plan(document_data)

    err = exc_info.value
    assert err.code == "UNASSIGNED_VOICE"
    assert err.speaker == "BOB"
    assert err.scene_number == 1
    assert err.utterance_index == 4


def test_build_playback_plan_missing_speaker_in_assignments():
    """If a speaker is completely omitted from voice_assignments, detect before playback."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voices/fake-narrator-1"},
            # ALICE is omitted
            {"speaker": "BOB", "voice_id": "voices/fake-bob-3"},
        ],
    }

    with pytest.raises(PlaybackPlanError) as exc_info:
        build_playback_plan(document_data)

    err = exc_info.value
    assert err.code == "UNASSIGNED_VOICE"
    assert err.speaker == "ALICE"
    assert err.scene_number == 1
    assert err.utterance_index == 2


def test_build_playback_plan_empty_script_raises():
    """Empty script with no utterances raises PlaybackPlanError."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": "",
        "voice_assignments": [],
    }

    with pytest.raises(PlaybackPlanError) as exc_info:
        build_playback_plan(document_data)

    assert exc_info.value.code == "NO_UTTERANCES"


def test_build_playback_plan_does_not_treat_japanese_narration_as_latin_cue():
    """Narration mentioning a character with an ASCII initial stays narrator-voiced."""
    source_fountain = """Title: 注文の多い料理店

EXT. 深い山奥 - 昼 #1#

鬱蒼とした木々が風にざわめいている。

@若い紳士A
おい、どうしたんだ。死んでるじゃないか。

紳士Aは犬の死体を冷たく見下ろし、ため息をつく。
あたりを見回しても、案内人の姿はどこにもない。
"""
    plan = build_playback_plan(
        {
            "document_id": "doc-test-japanese-prose",
            "version_id": 1,
            "source_fountain": source_fountain,
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/fake-narrator"},
                {"speaker": "若い紳士A", "voice_id": "voices/fake-gentleman-a"},
            ],
        }
    )

    assert [item.speaker for item in plan.utterances] == ["ナレーター", "ナレーター", "若い紳士A", "ナレーター"]
    assert "紳士Aは犬の死体" in plan.utterances[-1].text


def test_build_playback_plan_resolves_fullwidth_aliased_speaker_to_shortened_voice():
    """A cue carrying a full-width alias resolves a voice keyed on the short name.

    The Fountain parser strips only ASCII parentheses from cues, so an utterance
    for ``@奥の声（山猫たち）`` keeps the full name, while analysis may surface the
    shortened display name ``奥の声`` that the voice gets assigned to. Playback
    must reconcile the two instead of raising UNASSIGNED_VOICE.
    """
    source_fountain = """Title: 注文の多い料理店

EXT. 深い山奥 - 昼 #1#

風が木々を揺らしている。

@奥の声（山猫たち）
さあさあ、こっちへおいで。
"""
    plan = build_playback_plan(
        {
            "document_id": "doc-test-fullwidth-alias",
            "version_id": 1,
            "source_fountain": source_fountain,
            "voice_assignments": [
                {"speaker": "ナレーター", "voice_id": "voices/fake-narrator"},
                # Voice was created against the shortened, parens-stripped name.
                {"speaker": "奥の声", "voice_id": "voices/fake-okunokoe"},
            ],
        }
    )

    character_utterance = next(item for item in plan.utterances if item.target_type == "character")
    assert character_utterance.speaker == "奥の声（山猫たち）"
    assert character_utterance.voice_id == "voices/fake-okunokoe"
