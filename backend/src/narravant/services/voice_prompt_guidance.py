"""原文と人手評価に基づく生成ガイドと、明示した声質／読み方の区分。"""

from __future__ import annotations


def split_voice_traits(value: str) -> tuple[str, str]:
    """完全な2区分だけを解釈し、既存の自由文や曖昧な入力は変更しない。"""
    lines = value.splitlines()
    if len(lines) != 2:
        return value, ""
    for voice_label, delivery_label in (("声質:", "読み方:"), ("Voice:", "Delivery:")):
        if lines[0].startswith(voice_label) and lines[1].startswith(delivery_label):
            voice = lines[0][len(voice_label) :].strip()
            delivery = lines[1][len(delivery_label) :].strip()
            if voice and delivery:
                return voice, delivery
    return value, ""


def compose_delivery(default: str, local: str) -> str:
    """場面の速度・感情を優先しつつ、役の演技を消さない。"""
    if not default or not local:
        return local or default
    return (
        default + "\nCurrent line direction (overrides default pace, pauses and emotion): " + local
    )


def build_voice_traits_guidance() -> str:
    """原文と利用者の生成・聴取結果に基づく候補を日英の生成指示にする。"""
    return (
        "VOICE DESIGN AND DELIVERY GUIDANCE:\n"
        "Use source evidence only: source_character_cards.defining_traits or the screenplay itself. "
        "Never turn state_by_event, emotions, temporary illness, knowledge, or relationships into stable traits. "
        "Do not invent a numeric age or an age category from a name, pitch, appearance, or stereotype. "
        "Preserve the character-specific accent, vocal qualities, personality, and habitual manner; "
        "explicit source evidence overrides the candidate defaults below. Keep the character's identity, "
        "age and gender evidence intact in the source and character cards. Durable temperament may shape "
        "usual delivery; a current scene's emotion belongs to its local direction.\n"
        "For every voice_traits profile, write exactly TWO non-empty single-line sections, in the source "
        "language: Japanese uses '声質: ...' then '読み方: ...'; English uses 'Voice: ...' then 'Delivery: ...'. "
        "Separate them with one newline, without bullets, Markdown, extra labels, or blank lines. "
        "The first section goes unchanged to Voice Design; the second is the speaker's default TTS style. "
        "Use one or two concise sentences per section. Voice describes sound; Delivery describes usual "
        "pace, articulation and role portrayal. Age or role descriptions may appear in Delivery when "
        "source-supported; an acoustic-only profile is also valid. Adult female casting is optional, "
        "not compulsory for all children, and must never change the fictional character's age or gender.\n"
        "The following five profiles for school-age and preschool or early-speaking roles are the latest "
        "user-evaluated candidate palettes. Select by "
        "source-specific personality and vocal evidence, not by gender stereotypes. Do not force every "
        "boy to be active, every girl to be delicate, or every young character to have imprecise speech. "
        "Copy an example's numeric age ONLY if it agrees with the source; otherwise use the actual stated "
        "age or omit the number. Never invent eleven, twelve, thirteen, or a first-word stage. "
        "Do not infer pubertal stage from age alone. Do not infer speech-development stage from age alone. "
        "A preschool age, small appearance or a high pitch does not establish that speech is just emerging. "
        "Use the learning-to-speak and imprecise-articulation clauses below only when the source supports "
        "that stage or habitual speech. Otherwise retain suitable sound qualities with natural clear delivery.\n"
        "SOURCE-ESTABLISHED SCHOOL-AGE GIRL (around ten to thirteen): for a source-supported delicate, "
        "modest habitual manner, consider the following. Stronger or brisker source traits take precedence.\n"
        "声質: 透明感のある澄んだ高音域で、可憐さと繊細な響きを帯びた声。\n"
        "読み方: 十二、三歳の少女として、素直さとまっすぐさを込めて、澄んだ滑舌で慎ましく語る。\n"
        "Voice: A voice in a clear, transparent high register with a delicate, fine resonance.\n"
        "Delivery: As a girl of twelve or thirteen, speak candidly and directly, with clear articulation "
        "and modest delivery.\n"
        "SOURCE-ESTABLISHED SCHOOL-AGE BOY (around ten to thirteen, approximately thirteen or younger): "
        "for a source-supported lively, expressive habitual manner, consider this candidate; do not "
        "equate male identity with energetic delivery.\n"
        "声質: 芯のある明るくクリアな中高音域で、ハリと若々しいエネルギーを帯びた声。\n"
        "読み方: 十一、二歳の活発な少年として、感情を前面に出し、"
        "少し前のめりなテンポとハキハキとした明瞭な滑舌で語る。\n"
        "Voice: A bright, clear upper-middle register with a firm core, projection and youthful energy.\n"
        "Delivery: As an active boy of eleven or twelve, express feelings openly with a slightly forward-moving "
        "pace and crisp, clear articulation.\n"
        "For a quieter, plain-spoken source-supported boy, the earlier acoustic-only candidate remains "
        "an alternative. Voice/声質 and Delivery/読み方 for this acoustic-only candidate contain only sound "
        "and usual delivery, without invented adult casting or required age labels:\n"
        "声質: 若々しくハリのあるクリアな中高音域で、少し軽めのトーンを持つ中性的な声。\n"
        "読み方: 落ち着いたナチュラルなテンポで、抑揚を抑えつつ素朴でまっすぐな滑舌で語る。\n"
        "Voice: An androgynous voice with a youthful, firm, clear upper-middle register and a slightly light tone.\n"
        "Delivery: Speak at a calm, natural pace with restrained intonation and plain, direct articulation.\n"
        "SOURCE-ESTABLISHED YOUNG GIRL (preschool or early-primary role): use the speech-stage clauses "
        "only where the source supports them; omit them for an already fluent character.\n"
        "声質: 明るく澄んだメゾソプラノ帯域で、輪郭のはっきりしたクリアなトーン。\n"
        "読み方: まだ言葉をおぼえたての幼児。舌足らずな感じで、素直に語る。\n"
        "Voice: A bright, clear mezzo-soprano range with a well-defined, clean tone.\n"
        "Delivery: Portray a young character still learning words, speaking candidly with slightly imprecise "
        "articulation.\n"
        "SOURCE-ESTABLISHED YOUNG BOY (preschool or early-primary role): choose the lively attack only "
        "when it suits the source temperament, and the speech-stage clauses only when supported.\n"
        "声質: 芯のある軽快なメゾソプラノ帯域で、ハリのある明るくクリアなトーン。\n"
        "読み方: 少し話ができるようになった幼児。語頭を弾ませるようにハキハキと、舌足らずな感じで語る。\n"
        "Voice: A light mezzo-soprano range with a firm core and a bright, clear, projecting tone.\n"
        "Delivery: Portray a young character beginning to speak, with buoyant, crisp word onsets and slightly "
        "imprecise articulation.\n"
        "SOURCE-ESTABLISHED EARLY-SPEAKING INFANT: this is a separate candidate for a source-established "
        "infant or very young child who has begun speaking words, not an extrapolation to a preverbal baby. "
        "Do not invent a numeric age, gender, dialogue, crying, babble, coughs or vocal bursts.\n"
        "声質: 軽快で張りのあるメゾソプラノ帯域で、輪郭がくっきりとしたクリアなトーン。\n"
        "読み方: 少し話ができるようになった幼児。舌足らずで、たどたどしい感じ。\n"
        "Voice: A light, firm mezzo-soprano range with a sharply defined, clear tone.\n"
        "Delivery: Portray a very young character who has just begun speaking, with slightly imprecise, "
        "hesitant articulation.\n"
        "Dialogue wording also affects the perceived age, especially for early-speaking infants. A voice "
        "profile alone does not guarantee an infant impression for arbitrary dialogue. Do not rewrite the "
        "supplied dialogue or replace it with baby talk, omit words, or add phonetic spellings and extra "
        "sounds to manufacture that impression. Describe vocal delivery while preserving the supplied "
        "words. Do not force very slow, elongated delivery, extreme pitch, or detailed staccato control "
        "as a universal infant recipe; earlier tests sounded elderly or like an older boy.\n"
        "SOURCE-ESTABLISHED elderly ROLE (老人): candidate voice qualities are a weathered, thin, dry, "
        "breathy hoarse voice. For a source-established male impression, use light throat roughness and "
        "weak resonance; for a female impression, light roughness at voice onset. If gender is unspecified, "
        "keep it unspecified. Do not invent age seventy-five. Delivery: speak unhurriedly, placing words "
        "gently, with short pauses at natural meaning boundaries and soft phrase endings; quiet, unforced "
        "speech preserving the weathered hoarseness. No added coughs or exaggerated tremor; keep words "
        "clear. Do not override a source-established smooth voice or brisk habitual delivery.\n"
        "Earlier adult-performer options remain optional casting candidates where appropriate: a clear "
        "higher register, light bright or forward head resonance, round vowels and light consonants, with "
        "candid natural intonation. If that method is chosen, put performer casting only in Voice and "
        "the source-supported fictional role only in Delivery. Do not apply it automatically after refusal.\n"
        "SEPARATION: put resonance, register, breathiness, hoarseness, accent and any chosen performer "
        "casting in Voice/声質. Put usual pace, articulation, phrase endings, source-supported role and "
        "natural meaning-boundary pauses in Delivery/読み方. Do not put local emotions, urgency, emphasis, "
        "specific pause locations, new vocalizations or speech tags in either profile section. Those "
        "belong to the current line's Fountain parenthetical or approved inline tags; a current "
        "parenthetical overrides default delivery. Never copy a full profile into a parenthetical. "
        "Keep narrator identity inferred from the work's tone separate from a character's age category. "
        "Profiles without an age basis need a restrained natural voice and clear natural delivery.\n"
        "User-reported creation success does not establish a rejection mechanism or guaranteed acceptance. "
        "The latest Japanese feedback supports creation and approximate age impression for the five "
        "profiles, with dialogue-dependent limits for infants. English translations have not yet been "
        "verified with real audio. Do not infer universal forbidden-word rules, professional-rights "
        "filters, pitch-limit guards or session penalties from these observations. "
        "Do not automatically rewrite or retry rejected requests; preserve existing failure behavior.\n"
    )
