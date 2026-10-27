"""公式資料と利用者の方針に基づくstyle・局所タグの共通生成ガイド。"""

from __future__ import annotations

# 表記の正本はcore.speech_notation.AllowedPerformanceTag。
# 分類は生成用の用途説明で、音声効果を実測した一覧ではない。
TAG_SELECTION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("short pause", "long pause"),
        "A meaningful hesitation, withheld answer, realization, or disclosure beat. Choose the longer pause only "
        "when the source supports a distinctly longer silence. Do not duplicate punctuation, sentence boundaries, "
        "scene/speaker-change silence, or an existing pause; never promise exact timing.",
    ),
    (
        ("breath", "heavy breath", "exhales", "pant"),
        "An audible inhalation, labored breathing, exhalation, or panting supported by exertion or the scene's action. "
        "A breath is not a generic emphasis marker. Do not add normal breathing to every line.",
    ),
    (
        ("sigh", "sighs", "phew", "yawn"),
        "An audible sigh, relief exclamation, or yawn supported by release, frustration, fatigue or drowsiness. "
        "Relief alone does not require a sigh; prefer an abstract relieved style when no separate sound is needed.",
    ),
    (
        ("laugh", "laughter", "chuckle", "chuckles", "giggle", "cackle", "snicker", "snort"),
        "An actual burst of laughter. Select full laughter, a restrained chuckle, a light giggle, a cackle, a snicker "
        "or a snorting laugh only as the source supports. A friendly or happy line alone does not imply laughter; "
        "do not invent mockery, malice, childishness or a comic caricature.",
    ),
    (
        ("cry", "sob", "whimper"),
        "Audible crying, sobbing or whimpering supported by the scene. Sadness alone is insufficient. "
        "If emotion only colors the spoken words, use style rather than adding a crying sound.",
    ),
    (
        ("gasp", "argh", "groan", "moan"),
        "An audible gasp, frustrated/pained exclamation, groan or moan supported by surprise, distress or discomfort. "
        "Do not add pain, fear or other implications absent from the source.",
    ),
    (
        ("scream", "shout", "shriek", "cheer"),
        "A brief, localized scream, shout, shriek or cheer explicitly or strongly supported by the action. "
        "Do not turn an ordinary question into a scream. Sustained loud speech belongs in style.",
    ),
    (
        ("growl", "grunt", "grr", "hiss", "pff", "tsk"),
        "A human vocal reaction of effort, restrained frustration, disapproval or dismissal with strong source "
        "support. Use human vocalizations only; do not create animal noises, effects, new words or listener speech. "
        "Disapproval can usually remain an abstract acting intention without an added sound.",
    ),
    (
        ("cough", "sneeze", "throat-clearing"),
        "An actual cough, sneeze or throat clearing supported by the source at this point. Do not invent illness, "
        "an elderly stereotype, or throat clearing simply because someone begins to speak.",
    ),
    (
        ("whispers", "whispering"),
        "A localized whisper event when specifically supported. For a whole whispered line or sustained hush, "
        "use style instead. These tags are not paired span delimiters; do not invent closing tags or exact durations.",
    ),
)


def build_performance_guidance() -> str:
    """変換・執筆・局所改稿・完成稿レビューで同じ方針を使う。"""
    rules = "\n".join(
        ", ".join(f"<{tag}>" for tag in tags) + ": " + criterion for tags, criterion in TAG_SELECTION_RULES
    )
    return (
        "PERFORMANCE STYLE AND INLINE TAG POLICY (Google TTS, checked 2026-10-26):\n"
        "ABSTRACT INTENT FIRST: When a sustained direction is useful, prefer one concise, source-grounded "
        "emotion, attitude, subtext or communicative intention. Let TTS choose the acoustic realization. "
        "Examples: '相手を安心させるように', '不安を隠して確かめるように', '心から安堵して'; "
        "English: 'reassuring the other person', 'hiding anxiety while seeking reassurance', 'with genuine relief'. "
        "Do not automatically translate these into pitch, volume, speed, stress, breathiness or phrase-ending recipes. "
        "Abstract means an understandable acting intention, not an ungrounded metaphor or a long director's note. "
        "An empty style is valid where ordinary reading already conveys the intent.\n"
        "CONCRETE DELIVERY EXCEPTIONS: Both emotional and acoustic directions are supported. Add the minimum "
        "concrete delivery only when the source explicitly requires whispering, shouting, haste, slowness, "
        "or similar audible behavior, or a specific user/listening correction requires it. Preserve such source "
        "constraints; do not replace them with vague emotion. Do not claim abstract prompts always sound better. "
        "Use short natural language in the source language, not mandatory English or a required keyword list. "
        "Keep stable age, gender, timbre and accent in Voice Design; never copy a persona or voice-maintenance "
        "commands into style. Do not mechanically stack conflicting directions.\n"
        "SOURCE EVIDENCE BEFORE INSERTION: For each possible inline event, consider who actually produces "
        "the sound, the dialogue's intent, surrounding action, relationship and disclosure order, and the "
        "exact point where an audible event adds meaning. Use explicit or strong contextual evidence; "
        "an emotion label by itself is insufficient. Prefer no insertion if uncertain. Narrator narration "
        "about another person's breath or laughter must not make the narrator imitate that person's sound. "
        "Do not add a new non-speaking character turn.\n"
        "STYLE VERSUS EVENT: Sustained emotion/delivery belongs in the parenthetical/style; a distinct momentary "
        "human vocal sound or silence belongs inline at its actual position. Tag names stay in English even "
        "in Japanese speech. Select from every documented spelling below, including aliases, as appropriate. "
        "NEVER require a tag count or variety quota; zero tags is a valid reviewed result. Do not prefer sigh "
        "as a universal emotion marker. Preserve existing tags/directions according to the request.\n" + rules + "\n"
        "No music, applause, footsteps, animal effects or undocumented tags. Every rendered turn needs "
        "ordinary spoken text; no tag-only turns, no adjacent/duplicated pauses, and preserve existing "
        "listener/pipe notation without adding new overlap. Follow the current task\'s output format.\\n"
    )
