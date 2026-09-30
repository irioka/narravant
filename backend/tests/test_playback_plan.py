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


def test_build_playback_plan_success_ordering_and_speaker_types():
    """Verify playback plan establishes correct sequence, scene+index IDs, and voice assignments."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "ナレーター", "voice_id": "voices/fake-narrator-1"},
            {"speaker": "ALICE", "voice_id": "voices/fake-alice-2"},
            {"speaker": "BOB", "voice_id": "voices/fake-bob-3"},
        ],
    }

    plan = build_playback_plan(document_data)
    assert plan.document_id == "doc-test-1"
    assert plan.version_id == 1
    assert plan.total_utterances == 6
    assert len(plan.utterances) == 6

    # Scene 1, Utterance 0: Narration
    u0 = plan.utterances[0]
    assert u0.scene_number == 1
    assert u0.utterance_index == 0
    assert u0.speaker == "ナレーター"
    assert u0.target_type == "narrator"
    assert u0.text == "The room is dark and silent."
    assert u0.voice_id == "voices/fake-narrator-1"

    # Scene 1, Utterance 1: Alice dialogue
    u1 = plan.utterances[1]
    assert u1.scene_number == 1
    assert u1.utterance_index == 1
    assert u1.speaker == "ALICE"
    assert u1.target_type == "character"
    assert u1.text == "Is someone there?"
    assert u1.voice_id == "voices/fake-alice-2"

    # Scene 1, Utterance 2: Narration
    u2 = plan.utterances[2]
    assert u2.scene_number == 1
    assert u2.utterance_index == 2
    assert u2.speaker == "ナレーター"
    assert u2.text == "The grandfather clock ticks."

    # Scene 1, Utterance 3: Bob dialogue
    u3 = plan.utterances[3]
    assert u3.scene_number == 1
    assert u3.utterance_index == 3
    assert u3.speaker == "BOB"
    assert u3.voice_id == "voices/fake-bob-3"

    # Scene 2, Utterance 0: Narration
    u4 = plan.utterances[4]
    assert u4.scene_number == 2
    assert u4.utterance_index == 0
    assert u4.speaker == "ナレーター"
    assert u4.text == "Morning sunlight fills the yard."

    # Scene 2, Utterance 1: Alice dialogue
    u5 = plan.utterances[5]
    assert u5.scene_number == 2
    assert u5.utterance_index == 1
    assert u5.speaker == "ALICE"


def test_build_playback_plan_with_start_position():
    """Verify plan can filter from a specific start scene and utterance index (for resume/retry)."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "ナレーター", "voice_id": "voices/fake-narrator-1"},
            {"speaker": "ALICE", "voice_id": "voices/fake-alice-2"},
            {"speaker": "BOB", "voice_id": "voices/fake-bob-3"},
        ],
    }

    # Start from Scene 1, Utterance 2 (Bob's previous line onwards)
    plan = build_playback_plan(document_data, start_scene=1, start_utterance_index=2)
    assert plan.total_utterances == 6
    assert len(plan.utterances) == 4
    assert plan.utterances[0].scene_number == 1
    assert plan.utterances[0].utterance_index == 2

    # Start from Scene 2, Utterance 0
    plan_s2 = build_playback_plan(document_data, start_scene=2, start_utterance_index=0)
    assert len(plan_s2.utterances) == 2
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
            {"speaker": "ナレーター", "voice_id": "voices/fake-narrator-1"},
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
    assert err.utterance_index == 3


def test_build_playback_plan_missing_speaker_in_assignments():
    """If a speaker is completely omitted from voice_assignments, detect before playback."""
    document_data = {
        "document_id": "doc-test-1",
        "version_id": 1,
        "source_fountain": SAMPLE_FOUNTAIN,
        "voice_assignments": [
            {"speaker": "ナレーター", "voice_id": "voices/fake-narrator-1"},
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
    assert err.utterance_index == 1


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

    assert [item.speaker for item in plan.utterances] == ["ナレーター", "若い紳士A", "ナレーター"]
    assert "紳士Aは犬の死体" in plan.utterances[-1].text
