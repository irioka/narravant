"""研究から採用した声質／読み方の契約を合成データで検証する。"""

import json

import pytest

from narravant.core.fountain import FountainParser
from narravant.services.playback import build_playback_plan
from narravant.services.speech_segments import build_speech_segments
from narravant.services.tts import build_tts_request
from narravant.services.vertex_analysis import VertexDocumentAnalyzer
from narravant.services.voice_prompt_guidance import build_voice_traits_guidance, split_voice_traits


@pytest.mark.parametrize(
    ("traits", "voice", "delivery"),
    [
        (
            "声質: 軽い澄んだ声。\n読み方: 架空の幼児役として自然に話す。",
            "軽い澄んだ声。",
            "架空の幼児役として自然に話す。",
        ),
        ("Voice: A dry, breathy voice.\nDelivery: Speak unhurriedly.", "A dry, breathy voice.", "Speak unhurriedly."),
        ("Voice: Clear voice.\r\nDelivery: Natural delivery.", "Clear voice.", "Natural delivery."),
        ("普通の声。10歳。", "普通の声。10歳。", ""),
        ("A 75-year-old voice. Pause gently.", "A 75-year-old voice. Pause gently.", ""),
        ("Voice: \nDelivery: Slow.", "Voice: \nDelivery: Slow.", ""),
        ("Voice: Dry.\nDelivery: ", "Voice: Dry.\nDelivery: ", ""),
        ("Voice: Dry.\nDelivery: Slow.\nDelivery: Fast.", "Voice: Dry.\nDelivery: Slow.\nDelivery: Fast.", ""),
    ],
)
def test_explicit_sections_only_without_age_guessing(traits, voice, delivery):
    assert split_voice_traits(traits) == (voice, delivery)


@pytest.mark.parametrize(
    ("traits", "direction"),
    [
        ("声質: 合成された枯れ声。\n読み方: ゆったり静かに話す。", "ゆったり静かに話す。"),
        (
            "Voice: A clear adult actor voice.\nDelivery: Portray a fictional child naturally.",
            "Portray a fictional child naturally.",
        ),
    ],
)
def test_assigned_delivery_reaches_tts_style_without_entering_spoken_text(traits, direction):
    plan = build_playback_plan(
        {
            "source_fountain": "INT. ROOM - DAY #1#\n\n@ALICE\nHello. Goodbye.",
            "voice_assignments": [
                {"speaker": "Narrator", "voice_id": "voice_narrator"},
                {"speaker": "ALICE", "voice_id": "voice_alice", "voice_traits": traits},
            ],
        }
    )
    utterance = next(u for u in plan.utterances if u.speaker == "ALICE")
    segments = build_speech_segments(utterance)
    assert len(segments) == 2
    for segment in segments:
        payload = build_tts_request(segment, "synthetic", "en-US")
        part = payload["contents"][0]["parts"][0]
        assert part["speechMetadata"]["style"] == direction
        assert part["text"] in ("Hello.", "Goodbye.")
        assert traits not in json.dumps(payload, ensure_ascii=False)
    assert plan.utterances[0].performance_direction == ""  # 他話者に漏らさない


def test_scene_direction_overrides_default_delivery():
    plan = build_playback_plan(
        {
            "source_fountain": "INT. ROOM - DAY #1#\n\n@ALICE\n(urgently)\nRun!",
            "voice_assignments": [
                {"speaker": "Narrator", "voice_id": "voice_narrator"},
                {"speaker": "ALICE", "voice_id": "voice_alice", "voice_traits": "Voice: Dry.\nDelivery: Speak slowly."},
            ],
        }
    )
    u = next(u for u in plan.utterances if u.speaker == "ALICE")
    assert "Speak slowly." in u.performance_direction
    assert "overrides default pace" in u.performance_direction
    assert u.performance_direction.endswith("urgently")


@pytest.mark.parametrize("language", ["ja", "en"])
def test_analysis_uses_grounded_bilingual_research_guidance(language):
    prompt = VertexDocumentAnalyzer._analysis_prompt(
        FountainParser.parse("INT. ROOM - DAY #1#\n\n@ALICE\nHi."), max_main_characters=4, source_language=language
    )
    assert prompt.count(build_voice_traits_guidance()) == 1
    guidance = build_voice_traits_guidance()
    for term in (
        "preschool",
        "school-age",
        "elderly",
        "source evidence",
        "defining_traits",
        "state_by_event",
        "Voice:",
        "Delivery:",
        "声質:",
        "読み方:",
    ):
        assert term in guidance
    assert "Do not invent a numeric age" in guidance
    assert "character-specific accent" in guidance


def test_source_grounded_boy_candidates_reach_analysis_prompt_without_refusal_rewriting():
    prompt = VertexDocumentAnalyzer._analysis_prompt(
        FountainParser.parse("INT. ROOM - DAY #1#\n\n@AKI\nHello."),
        max_main_characters=4, source_language="ja",
    )
    for term in (
        "SOURCE-ESTABLISHED SCHOOL-AGE BOY",
        "approximately thirteen or younger",
        "Do not infer pubertal stage from age alone",
        "若々しくハリのあるクリアな中高音域",
        "落ち着いたナチュラルなテンポ",
        "芯のある明るくクリアな中高音域",
        "ハキハキとした明瞭な滑舌",
        "youthful, firm, clear upper-middle register",
        "calm, natural pace",
        "source-specific personality",
        "Do not automatically rewrite or retry rejected requests",
    ):
        assert term in prompt
    assert "Voice/声質 and Delivery/読み方 for this acoustic-only candidate" in prompt
    assert "User-reported creation success" in prompt


@pytest.mark.parametrize(
    ("traits", "voice", "delivery"),
    [
        (
            "声質: 若々しくハリのあるクリアな中高音域で、少し軽めのトーンを持つ中性的な声。\n"
            "読み方: 落ち着いたナチュラルなテンポで、抑揚を抑えつつ素朴でまっすぐな滑舌で語る。",
            "若々しくハリのあるクリアな中高音域で、少し軽めのトーンを持つ中性的な声。",
            "落ち着いたナチュラルなテンポで、抑揚を抑えつつ素朴でまっすぐな滑舌で語る。",
        ),
        (
            "Voice: An androgynous voice with a youthful, firm, clear upper-middle register "
            "and a slightly light tone.\n"
            "Delivery: Speak at a calm, natural pace with restrained intonation and plain, direct articulation.",
            "An androgynous voice with a youthful, firm, clear upper-middle register and a slightly light tone.",
            "Speak at a calm, natural pace with restrained intonation and plain, direct articulation.",
        ),
    ],
)
def test_acoustic_boy_candidates_keep_voice_and_delivery_separate(traits, voice, delivery):
    assert split_voice_traits(traits) == (voice, delivery)


def test_final_user_evaluated_profiles_and_early_speech_scope_reach_generation():
    guidance = build_voice_traits_guidance()
    for phrase in (
        "SOURCE-ESTABLISHED SCHOOL-AGE GIRL",
        "SOURCE-ESTABLISHED YOUNG GIRL",
        "SOURCE-ESTABLISHED YOUNG BOY",
        "SOURCE-ESTABLISHED EARLY-SPEAKING INFANT",
        "透明感のある澄んだ高音域で、可憐さと繊細な響きを帯びた声",
        "十一、二歳の活発な少年として",
        "まだ言葉をおぼえたての幼児。舌足らずな感じで、素直に語る",
        "少し話ができるようになった幼児。語頭を弾ませるようにハキハキと",
        "少し話ができるようになった幼児。舌足らずで、たどたどしい感じ",
        "clear, transparent high register",
        "slightly imprecise articulation",
        "Do not infer speech-development stage from age alone",
        "Dialogue wording also affects the perceived age",
        "Do not rewrite the supplied dialogue",
        "creation and approximate age impression",
    ):
        assert phrase in guidance
    assert "must describe ONLY the adult performer" not in guidance


@pytest.mark.parametrize("line", ["まま、あっち。", "Mama, over there."])
def test_early_speech_profile_changes_only_style_and_preserves_supplied_dialogue(line):
    traits = (
        "声質: 軽快で張りのあるメゾソプラノ帯域で、輪郭がくっきりとしたクリアなトーン。"
        "\n読み方: 少し話ができるようになった幼児。舌足らずで、たどたどしい感じ。"
    )
    plan = build_playback_plan({
        "source_fountain": "INT. ROOM - DAY #1#\n\n@MIO\n" + line,
        "voice_assignments": [
            {"speaker": "Narrator", "voice_id": "voice_narrator"},
            {"speaker": "MIO", "voice_id": "voice_mio", "voice_traits": traits},
        ],
    })
    utterance = next(u for u in plan.utterances if u.speaker == "MIO")
    payloads = [build_tts_request(segment, "synthetic", "ja-JP") for segment in build_speech_segments(utterance)]
    parts = [payload["contents"][0]["parts"][0] for payload in payloads]
    assert "".join(part["text"] for part in parts) == line
    assert all(part["speechMetadata"]["style"] == split_voice_traits(traits)[1] for part in parts)


def test_english_document_routes_short_utterances_to_english_tts():
    source = (
        "INT. ROOM - DAY #1#\n\n@ALICE\n"
        "The window was open and she knew that they were waiting with her.\n\n@ALICE\nYes."
    )
    plan = build_playback_plan(
        {
            "source_fountain": source,
            "voice_assignments": [
                {"speaker": "Narrator", "voice_id": "voice_narrator"},
                {"speaker": "ALICE", "voice_id": "voice_alice", "voice_traits": "Voice: Clear.\nDelivery: Natural."},
            ],
        }
    )
    segment = build_speech_segments(plan.utterances[-1])[0]
    assert segment.text == "Yes."
    assert segment.language_code == "en-US"
    payload = build_tts_request(segment, "synthetic", "ja-JP")
    assert payload["generationConfig"]["speechConfig"]["languageCode"] == "en-US"
