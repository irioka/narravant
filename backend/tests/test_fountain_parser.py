"""Spike & Stabilize behavioral tests for FountainParser."""

from narravant.core.fountain import FountainParser, sanitize_fountain_text

SAMPLE_FOUNTAIN_SCRIPT = """Title: The Neon Detective
Credit: Written by
Author: Rei Kisaragi
Format: 映画
Genre: Sci-Fi
Genre2: Mystery
Synopsis: In 2088 Neo-Tokyo, a cynical detective tracks a runaway AI.
Characters: KEN, LUNA, BOSS

# Act 1: The Incident

INT. DETECTIVE OFFICE - NIGHT #1#
Rain hammers against the cracked neon window.
KEN sits behind a cluttered desk, examining a glowing data cube.

KEN
(sighing)
Another ghost in the machine.

LUNA
(stepping out from the shadow)
Not a ghost. A witness.

EXT. CYBER STREETS - NIGHT #2#
Ken and Luna navigate through the crowded cyber market.
Drones buzz overhead.

.UNDERGROUND LAB #3#
Rows of servers hum with eerie blue light.
KEN draws his pulse pistol.

KEN
Stay behind me.
"""


def test_sanitize_fountain_text():
    raw = (
        'Normal text[{"type":"text","text":"","extras":{"signature":"anthropic-sig-123"}}]'
        "[FOUNTAIN_CONVERSION_COMPLETE]"
    )
    cleaned = sanitize_fountain_text(raw)
    assert "signature" not in cleaned
    assert "[FOUNTAIN_CONVERSION_COMPLETE]" not in cleaned
    assert "Normal text" in cleaned


def test_parse_scene_headings():
    is_hd, num, text = FountainParser.parse_scene_heading("INT. OFFICE - DAY #12#")
    assert is_hd is True
    assert num == 12
    assert text == "INT. OFFICE - DAY"

    is_hd, num, text = FountainParser.parse_scene_heading("EXT. ROOFTOP - NIGHT")
    assert is_hd is True
    assert num is None
    assert text == "EXT. ROOFTOP - NIGHT"

    is_hd, num, text = FountainParser.parse_scene_heading(".MYSTERIOUS ALLEY #99#")
    assert is_hd is True
    assert num == 99
    assert text == "MYSTERIOUS ALLEY"

    # Numbered heading without leading dot must also be recognized
    is_hd, num, text = FountainParser.parse_scene_heading("清瀬家・玄関 #1#")
    assert is_hd is True
    assert num == 1
    assert text == "清瀬家・玄関"

    # Standard prefixes with dot or space, case-insensitive
    prefixes_to_test = [
        ("EST. TOKYO SKYLINE - SUNSET #3#", 3, "EST. TOKYO SKYLINE - SUNSET"),
        ("EST TOKYO SKYLINE - SUNSET #4#", 4, "EST TOKYO SKYLINE - SUNSET"),
        ("INT./EXT. POLICE CAR - DAY #5#", 5, "INT./EXT. POLICE CAR - DAY"),
        ("INT/EXT. MOVING TRAIN - NIGHT #6#", 6, "INT/EXT. MOVING TRAIN - NIGHT"),
        ("I/E. SUBWAY STATION - MORNING #7#", 7, "I/E. SUBWAY STATION - MORNING"),
        ("I/E CAFE - AFTERNOON #8#", 8, "I/E CAFE - AFTERNOON"),
        ("int. small room - dawn #9#", 9, "int. small room - dawn"),
        ("ext open field - dusk #10#", 10, "ext open field - dusk"),
        ("est. city view - night", None, "est. city view - night"),
        ("int/ext highway - day", None, "int/ext highway - day"),
    ]
    for raw_heading, expected_num, expected_text in prefixes_to_test:
        is_hd, num, text = FountainParser.parse_scene_heading(raw_heading)
        assert is_hd is True, f"Failed for {raw_heading}"
        assert num == expected_num, f"Failed num for {raw_heading}"
        assert text == expected_text, f"Failed text for {raw_heading}"

    # Forced action line with ! must NOT be a heading
    is_hd, _, _ = FountainParser.parse_scene_heading("!INT. EXPLOSION SHOT")
    assert is_hd is False
    is_hd, _, _ = FountainParser.parse_scene_heading("!EST. BEAUTIFUL HORIZON")
    assert is_hd is False


def test_full_script_parsing_and_v1_json():
    parsed = FountainParser.parse(SAMPLE_FOUNTAIN_SCRIPT)

    # Verify metadata
    assert parsed.metadata.title == "The Neon Detective"
    assert parsed.metadata.format == "映画"
    assert parsed.metadata.genre1 == "Sci-Fi"
    assert "KEN" in parsed.character_names()
    assert "LUNA" in parsed.character_names()

    # Verify scenes
    assert parsed.scene_count() == 3
    s1 = parsed.scenes[0]
    assert s1.scene_number == 1
    assert s1.heading == "INT. DETECTIVE OFFICE - NIGHT"
    assert len(s1.dialogues) == 2
    assert s1.dialogues[0].character == "KEN"
    assert "Another ghost in the machine" in s1.dialogues[0].line
    assert s1.dialogues[1].character == "LUNA"

    s2 = parsed.scenes[1]
    assert s2.scene_number == 2
    assert s2.heading == "EXT. CYBER STREETS - NIGHT"

    s3 = parsed.scenes[2]
    assert s3.scene_number == 3
    assert s3.heading == "UNDERGROUND LAB"

    # Verify v1 JSON schema export
    v1_doc = parsed.to_v1_json(
        document_id="test-doc-123",
        owner_user_id="owner-1",
        source={
            "filename": "test.fountain",
            "media_type": "text/x-fountain",
            "sha256": "a" * 64,
        },
        analysis={"status": "completed", "turning_points": [], "characters": []},
        version_id=1,
    )
    assert v1_doc["schema_version"] == 1
    assert v1_doc["document_id"] == "test-doc-123"
    assert len(v1_doc["scenes"]) == 3
    assert v1_doc["metadata"]["title"] == "The Neon Detective"
    assert v1_doc["source_fountain"] == SAMPLE_FOUNTAIN_SCRIPT.strip()


def test_narrator_cue_and_parenthetical_performance_direction():
    """@ナレーター cues become narrator utterances; parentheticals become performance directions (not spoken)."""
    script = """Title: メロス

EXT. シラクスの市街 - 昼 #1#

@ナレーター
(落ち着いた低い声で、ゆっくりと)
南欧の陽光が降り注ぐ街並み。人々は口を噤んで歩いている。

@メロス
(不安げに、早口で)
何かがおかしい。まるで町全体が死に絶えたかのようだ。
"""
    parsed = FountainParser.parse(script)
    utterances = parsed.all_utterances()
    assert len(utterances) == 2

    narrator = utterances[0]
    assert narrator.speaker == "ナレーター"
    assert narrator.target_type == "narrator"
    assert narrator.performance_direction == "落ち着いた低い声で、ゆっくりと"
    # The parenthetical itself must not appear in the spoken text.
    assert "落ち着いた低い声で" not in narrator.text
    assert "南欧の陽光" in narrator.text

    melos = utterances[1]
    assert melos.speaker == "メロス"
    assert melos.target_type == "character"
    assert melos.performance_direction == "不安げに、早口で"
    assert "何かがおかしい" in melos.text


def test_fullwidth_parenthetical_is_performance_direction_for_narrator_and_character():
    """Japanese Fountain brackets must not leak into the audition speech."""
    script = """Title: 全角括弧

INT. 部屋 - 夜 #1#

@ナレーター
（不安げに）

ここはどこだ。

@メロス
（決意を込めて）

必ず戻る。
"""

    utterances = FountainParser.parse(script).all_utterances()

    assert [(item.speaker, item.performance_direction, item.text) for item in utterances] == [
        ("ナレーター", "不安げに", "ここはどこだ。"),
        ("メロス", "決意を込めて", "必ず戻る。"),
    ]


def test_japanese_narration_with_latin_name_does_not_become_character_cue():
    """Latin initials inside Japanese prose must not turn narration into a speaker."""
    script = """Title: 注文の多い料理店

EXT. 深い山奥 - 昼 #1#

@若い紳士A
おい、どうしたんだ。……死んでるじゃないか。

紳士Aは犬の死体を冷たく見下ろし、ため息をつく。
あたりを見回しても、案内人の姿はどこにもない。
"""

    utterances = FountainParser.parse(script).all_utterances()

    assert [item.speaker for item in utterances] == ["若い紳士A", "ナレーター"]
    assert "紳士Aは犬の死体" in utterances[1].text


def test_dialogue_character_names_include_all_non_narrator_cues():
    script = """Title: 話者一覧

INT. 部屋 - 昼 #1#

@ナレーター
（静かに）
部屋には誰もいない。

@専門の猟師
見つけたぞ。

@通行人
助けて！
"""

    parsed = FountainParser.parse(script)

    assert parsed.dialogue_character_names() == ["専門の猟師", "通行人"]


def test_contd_cues_are_collapsed_to_single_character() -> None:
    """CONT'D cues with ASCII or curly apostrophe collapse to the same character."""
    script = """Title: Contd Test

INT. ROOM - DAY #1#

@MAYA
(quietly)
First line.

@MAYA (CONT'D)
Second line.

BOB
(calmly)
Third line.

BOB (CONT’D)
Fourth line.
"""
    parsed = FountainParser.parse(script)
    assert parsed.dialogue_character_names() == ["MAYA", "BOB"]
    utterances = [u for u in parsed.all_utterances() if u.target_type == "character"]
    assert len(utterances) == 4
    assert [u.speaker for u in utterances] == ["MAYA", "MAYA", "BOB", "BOB"]
    assert [u.text for u in utterances] == ["First line.", "Second line.", "Third line.", "Fourth line."]
