const SCENE_HEADING = /^(?:INT\.\/EXT|INT\/EXT|INT|EXT|EST|I\/E)(?:\.|\s)/im
const SPOKEN_SCENE_HEADING_PREFIX = /^(?:INT\.\/EXT\.?|INT\/EXT\.?|I\/E\.?|INT\.?|EXT\.?|EST\.?)(?:\s+|$)/i
const SCENE_NUMBER_SUFFIX = /\s*#\d+#\s*$/

// Japanese hiragana, katakana, and CJK Unified Ideographs — same detection used
// by the backend's _wrap_bare_narration to pick @ナレーター vs @Narrator.
const JAPANESE_CHARS = /[\u3040-\u30ff\u3400-\u9fff]/

export function isNarratorSpeaker(speaker: string): boolean {
  return speaker === 'ナレーター' || speaker === 'Narrator' || speaker === 'NARRATOR'
}

/**
 * Return the narrator cue name used in this Fountain source.
 *
 * The backend conversion prompt instructs the LLM to write "@ナレーター" for
 * Japanese sources and "@Narrator" for English sources; the post-processing
 * safety net (_wrap_bare_narration) uses the same Japanese-character test.
 * The frontend uses this helper wherever the narrator speaker key is needed so
 * that voice_assignments, missingVoiceSpeakers, and playback all agree on the
 * same cue name, regardless of UI language setting.
 */
export function narratorSpeakerName(fountain: string): string {
  // 明示 cue がある場合は、日本語タイトルや固有名詞による言語推定より優先する。
  for (const line of fountain.split(/\r?\n/)) {
    const cue = /^@(.+)$/.exec(line.trim())
    if (!cue) continue
    const speaker = cueSpeakerName(cue[1])
    if (isNarratorSpeaker(speaker)) return speaker
  }
  return JAPANESE_CHARS.test(fountain) ? 'ナレーター' : 'Narrator'
}

/**
 * Normalize a Fountain character cue to the speaker name used elsewhere.
 *
 * The backend parser (FountainParser.parse) strips only a trailing ASCII
 * parenthetical from a cue, so a full-width alias such as `専門の猟師（案内人）`
 * is kept verbatim as the speaker name on utterances and character profiles.
 * The frontend must strip the same way; stripping full-width parentheses here
 * too would yield `専門の猟師`, which no longer matches the backend profile name
 * and causes a duplicate, empty character to be synthesized in the voice UI.
 */
export function cueSpeakerName(cue: string): string {
  return cue.trim().replace(/\s*\(.*?\)\s*$/, '')
}

export function countSceneHeadings(text: string): number {
  return text.split(/\r?\n/).filter((line) => SCENE_HEADING.test(line)).length
}

export interface TextRange {
  start: number
  end: number
}

export interface PlaybackStartPosition {
  sceneNumber: number
  utteranceIndex: number
}

interface SourceLine {
  text: string
  start: number
  end: number
}

function sourceLines(source: string): SourceLine[] {
  const lines: SourceLine[] = []
  let offset = 0
  for (const line of source.split(/\n/)) {
    const text = line.endsWith('\r') ? line.slice(0, -1) : line
    lines.push({ text, start: offset, end: offset + text.length })
    offset += line.length + 1
  }
  return lines
}

function sceneNumberFromHeading(line: string, fallback: number): number {
  const match = /#(\d+)#\s*$/.exec(line)
  return match ? Number(match[1]) : fallback
}

/** Return the source range of a scene heading for native-text selection. */
export function sceneHeadingRange(source: string, sceneNumber: number): TextRange | undefined {
  let fallback = 1
  for (const line of sourceLines(source)) {
    if (!isSceneHeading(line.text.trim())) continue
    const current = sceneNumberFromHeading(line.text.trim(), fallback)
    fallback = current + 1
    if (current === sceneNumber) return { start: line.start, end: line.end }
  }
  return undefined
}

/** Return the source range for the ordered utterance emitted by the playback plan. */
export function utteranceRange(source: string, sceneNumber: number, utteranceIndex: number): TextRange | undefined {
  const matches = playbackUtteranceRanges(source).filter((item) => item.scene === sceneNumber)
  return matches[utteranceIndex]?.range
}

function spokenSceneHeading(line: SourceLine): { text: string; range: TextRange } | undefined {
  const trimmed = line.text.trim()
  if (!trimmed) return undefined
  const lineOffset = line.text.indexOf(trimmed)
  const withoutNumber = trimmed.replace(SCENE_NUMBER_SUFFIX, '')
  const classification = SPOKEN_SCENE_HEADING_PREFIX.exec(withoutNumber)
  const contentStart = classification?.[0].length ?? 0
  const content = withoutNumber.slice(contentStart)
  const text = content.trim()
  if (!text) return undefined
  const leadingContentWhitespace = content.length - content.trimStart().length
  const start = line.start + lineOffset + contentStart + leadingContentWhitespace
  return { text, range: { start, end: start + text.length } }
}

/** Return the playback utterance containing (or immediately following) a text cursor. */
export function playbackStartAtOffset(source: string, offset: number): PlaybackStartPosition | undefined {
  const ranges = playbackUtteranceRanges(source)
  if (ranges.length === 0) return undefined

  const boundedOffset = Math.max(0, Math.min(offset, source.length))
  const selected = ranges.find(({ range }) => range.start <= boundedOffset && boundedOffset <= range.end)
    ?? ranges.find(({ range }) => range.start >= boundedOffset)
    ?? ranges[ranges.length - 1]
  const utteranceIndex = ranges
    .slice(0, ranges.indexOf(selected))
    .filter((item) => item.scene === selected.scene)
    .length
  return { sceneNumber: selected.scene, utteranceIndex }
}

function playbackUtteranceRanges(source: string): Array<{ scene: number; range: TextRange }> {
  const ranges: Array<{ scene: number; range: TextRange }> = []
  let fallbackScene = 1
  let currentScene: number | undefined
  let currentCue: string | undefined
  let pending: TextRange | undefined
  let pendingIsDialogue = false

  const flush = () => {
    const hadPending = Boolean(pending)
    if (currentScene !== undefined && pending) ranges.push({ scene: currentScene, range: pending })
    pending = undefined
    pendingIsDialogue = false
    if (hadPending) currentCue = undefined
  }

  for (const line of sourceLines(source)) {
    const text = line.text.trim()
    if (isSceneHeading(text)) {
      flush()
      currentScene = sceneNumberFromHeading(text, fallbackScene)
      fallbackScene = currentScene + 1
      currentCue = undefined
      const heading = spokenSceneHeading(line)
      if (heading) ranges.push({ scene: currentScene, range: heading.range })
      continue
    }
    if (currentScene === undefined || isTitlePageLine(text)) continue
    if (text === '') {
      if (currentCue && pendingIsDialogue && pending) flush()
      else if (!currentCue) flush()
      continue
    }
    const cue = /^@(.+)$/.exec(text)
    if (cue) {
      flush()
      currentCue = cueSpeakerName(cue[1])
      continue
    }
    if (isPerformanceDirection(text)) continue
    if (currentCue) {
      pendingIsDialogue = true
      pending = pending ? { start: pending.start, end: line.end } : { start: line.start, end: line.end }
    } else {
      pending = pending ? { start: pending.start, end: line.end } : { start: line.start, end: line.end }
    }
  }
  flush()
  return ranges
}

export function hasSceneHeadings(text: string): boolean {
  return SCENE_HEADING.test(text)
}

/** A Fountain scene heading line, used to add its speakable content to narration. */
function isSceneHeading(line: string): boolean {
  return SCENE_HEADING.test(line)
}

/** A Title-page style key line (e.g. "Title: ...") that should not be spoken. */
function isTitlePageLine(line: string): boolean {
  return /^[A-Za-z][A-Za-z ]*:\s/.test(line)
}

/** Fountain parentheticals guide delivery and must never become sample speech. */
function isPerformanceDirection(line: string): boolean {
  return /^(?:\(|（).*(?:\)|）)$/.test(line)
}

/** Return non-narrator character cues that contain a spoken line. */
export function spokenCharacterNames(fountain: string): string[] {
  const names: string[] = []
  let currentCue: string | undefined
  let cueHasSpeech = false

  const flush = () => {
    if (
      currentCue &&
      cueHasSpeech &&
      !isNarratorSpeaker(currentCue) &&
      !names.includes(currentCue)
    ) {
      names.push(currentCue)
    }
    currentCue = undefined
    cueHasSpeech = false
  }

  for (const raw of fountain.split(/\r?\n/)) {
    const line = raw.trim()
    const cue = /^@(.+)$/.exec(line)
    if (cue) {
      flush()
      currentCue = cueSpeakerName(cue[1])
      continue
    }
    if (line === '' || isSceneHeading(line) || isTitlePageLine(line)) {
      if (line === '' && currentCue && !cueHasSpeech) continue
      flush()
      continue
    }
    if (currentCue && !isPerformanceDirection(line)) cueHasSpeech = true
  }
  flush()
  return names
}

/**
 * Return the first sentence/line spoken (or narrated) by `speaker` in the Fountain
 * source, for the "check text" preview. Character dialogue is introduced by a
 * `@Name` cue line; the narrator speaks both explicit `@ナレーター`/`@Narrator`
 * blocks and plain narration lines that are not under a character cue. Fountain
 * parentheticals are delivery instructions rather than text to audition.
 *
 * Returns an empty string when nothing matching is found.
 */
export function firstUtteranceLine(
  fountain: string,
  speaker: string,
  narratorSpeaker: string,
): string {
  const lines = fountain.split(/\r?\n/)
  const wantNarrator = speaker === narratorSpeaker || isNarratorSpeaker(speaker)
  let currentSpeaker: string | undefined
  let currentCueHasDialogue = false

  for (const raw of lines) {
    const line = raw.trim()
    if (line === '') {
      // Fountain commonly puts a blank line between a cue, its parenthetical,
      // and the spoken line. Keep that cue alive until a spoken line has been
      // consumed; after a dialogue block, the blank line returns to narration.
      if (currentCueHasDialogue) {
        currentSpeaker = undefined
        currentCueHasDialogue = false
      }
      continue
    }
    const cue = /^@(.+)$/.exec(line)
    if (cue) {
      currentSpeaker = cueSpeakerName(cue[1])
      currentCueHasDialogue = false
      continue
    }
    if (isSceneHeading(line)) {
      if (wantNarrator) {
        const heading = spokenSceneHeading({ text: raw, start: 0, end: raw.length })
        if (heading) return firstSentence(heading.text)
      }
      currentSpeaker = undefined
      currentCueHasDialogue = false
      continue
    }
    if (isTitlePageLine(line)) {
      currentSpeaker = undefined
      currentCueHasDialogue = false
      continue
    }
    if (isPerformanceDirection(line)) continue
    if (wantNarrator) {
      if (currentSpeaker === undefined || currentSpeaker === narratorSpeaker || isNarratorSpeaker(currentSpeaker)) {
        currentCueHasDialogue = currentSpeaker !== undefined
        return firstSentence(line)
      }
      currentCueHasDialogue = true
      continue
    }
    if (currentSpeaker === speaker) {
      currentCueHasDialogue = true
      return firstSentence(line)
    }
    if (currentSpeaker !== undefined) currentCueHasDialogue = true
  }
  return ''
}

/** Take the first sentence of a line, splitting on Japanese/Latin sentence enders. */
function firstSentence(line: string): string {
  const match = /^.*?[。．.!！?？]/.exec(line)
  return (match ? match[0] : line).trim()
}
