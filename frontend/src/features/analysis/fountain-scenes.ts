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

function normalizeSpeakerAlias(speaker: string): string {
  return speaker.replace(/[\(（\[［].*?[\)）\]］]/g, '').replace(/\s/g, '').trim()
}

const LISTENER_NOTE_PREFIX = '[[listener:'
const LISTENER_NOTE = /^\[\[listener:([^\[\]\r\n]+)\]\]$/
const SPEECH_TAGS = /<(?:short pause|long pause|breath|sigh|laugh|chuckle|gasp|sob|cough|throat-clearing)>/g
const SENTENCE_ENDINGS = new Set('。．.!！？?？…')
const CLOSING_PUNCTUATION = new Set('」』）)]}’"〉》')

function isListenerNoteCandidate(line: string): boolean {
  return line.toLowerCase().startsWith(LISTENER_NOTE_PREFIX)
}

function listenerFromNote(line: string): string | undefined {
  const match = LISTENER_NOTE.exec(line)
  return match?.[1].trim() || undefined
}

function isFountainNote(line: string): boolean {
  return line.startsWith('[[')
}

function stripSpeechTags(text: string): string {
  return text.replace(SPEECH_TAGS, '')
}

function listenerSpeech(line: string, listener: string | undefined): { main: string; reactions: string[] } {
  if (!listener) return { main: stripSpeechTags(line), reactions: [] }
  const reactions: string[] = []
  const main = line.replace(/\|([^|]+)\|/g, (_match, reaction: string) => {
    reactions.push(stripSpeechTags(reaction))
    return ''
  })
  return { main: stripSpeechTags(main), reactions }
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
  sourceOffset?: number
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
export function utteranceRange(source: string, sceneNumber: number, utteranceIndex: number, sourceOffset?: number): TextRange | undefined {
  const matches = playbackUtteranceRanges(source).filter((item) => item.scene === sceneNumber)
  const range = matches[utteranceIndex]?.range
  if (!range || sourceOffset === undefined || sourceOffset <= range.start || sourceOffset >= range.end) return range
  return { start: sourceOffset, end: range.end }
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
  const position = { sceneNumber: selected.scene, utteranceIndex }
  const sourceOffset = sentenceStartAtOffset(source, selected.range, boundedOffset)
  return sourceOffset === undefined ? position : { ...position, sourceOffset }
}

/** 文カーソル位置より前の直近の文境界から、次の文の開始位置を返す。 */
function sentenceStartAtOffset(source: string, range: TextRange, offset: number): number | undefined {
  const headingLine = sourceLines(source).find((line) =>
    line.start <= range.start && range.start <= line.end,
  )
  if (headingLine && isSceneHeading(headingLine.text.trim())) return undefined

  const utteranceText = source.slice(range.start, range.end)
  let sentenceStart = 0
  for (let index = 0; index < utteranceText.length; index += 1) {
    const ending = utteranceText[index]
    if (!SENTENCE_ENDINGS.has(ending) && ending !== '.' && ending !== '．') continue
    if (ending === '.') {
      const token = utteranceText.slice(0, index + 1).split(/\s+/).at(-1)?.toLowerCase()
      if (token && ['dr.', 'mr.', 'mrs.', 'ms.', 'prof.', 'sr.', 'e.g.', 'i.e.'].includes(token)) continue
    }

    let boundaryEnd = index + 1
    while (boundaryEnd < utteranceText.length && CLOSING_PUNCTUATION.has(utteranceText[boundaryEnd])) {
      boundaryEnd += 1
    }
    if (
      (ending === '.' || ending === '．') &&
      boundaryEnd < utteranceText.length &&
      !/\s/.test(utteranceText[boundaryEnd])
    ) {
      continue
    }

    const boundaryOffset = range.start + boundaryEnd
    while (boundaryEnd < utteranceText.length && /\s/.test(utteranceText[boundaryEnd])) {
      boundaryEnd += 1
    }
    if (boundaryEnd < utteranceText.length && offset >= boundaryOffset) {
      sentenceStart = boundaryEnd
    }
  }

  const result = range.start + sentenceStart
  return result > range.start && source.slice(result, range.end).trim() ? result : undefined
}

/**
 * 指定した発話内のカーソルより前を原稿から除き、再生計画の発話 index は保つ。
 * 呼出元は下書き本文にだけ適用し、保存済み文書の認可経路を変更しない。
 */
export function playbackSourceAtPosition(source: string, position: PlaybackStartPosition): string {
  if (position.sourceOffset === undefined) return source
  const range = utteranceRange(source, position.sceneNumber, position.utteranceIndex)
  if (!range || position.sourceOffset <= range.start || position.sourceOffset > range.end) return source
  return source.slice(0, range.start) + source.slice(position.sourceOffset)
}

function playbackUtteranceRanges(source: string): Array<{ scene: number; range: TextRange }> {
  const ranges: Array<{ scene: number; range: TextRange }> = []
  let fallbackScene = 1
  let currentScene: number | undefined
  let currentCue: string | undefined
  let inRegularNote = false
  let pending: TextRange | undefined
  let pendingIsDialogue = false

  const flush = () => {
    const hadPending = Boolean(pending)
    if (currentScene !== undefined && pending) ranges.push({ scene: currentScene, range: pending })
    pending = undefined
    pendingIsDialogue = false
    if (hadPending) {
      currentCue = undefined
    }
  }

  for (const line of sourceLines(source)) {
    const text = line.text.trim()
    if (inRegularNote) {
      if (text.includes(']]')) inRegularNote = false
      continue
    }
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
    if (isListenerNoteCandidate(text) || isFountainNote(text)) {
      if (isFountainNote(text) && !text.includes(']]')) inRegularNote = true
      continue
    }
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
  // 同じ人物の発話回数を、別名解決の候補人数として数えない。
  const cueNames = [...new Set(fountain.split(/\r?\n/)
    .map((line) => /^@(.+)$/.exec(line.trim()))
    .filter((cue): cue is RegExpExecArray => cue !== null)
    .map((cue) => cueSpeakerName(cue[1])))]
  let currentCue: string | undefined
  let cueHasSpeech = false
  let currentListener: string | undefined
  let cueHasReaction = false
  let inRegularNote = false

  const flush = () => {
    if (currentCue && cueHasSpeech && !isNarratorSpeaker(currentCue) && !names.includes(currentCue)) {
      names.push(currentCue)
    }
    if (cueHasReaction && currentListener && !isNarratorSpeaker(currentListener)) {
      const normalizedListener = normalizeSpeakerAlias(currentListener)
      const matches = cueNames.filter((name) => normalizeSpeakerAlias(name) === normalizedListener)
      const resolvedListener = matches.length === 1 ? matches[0] : currentListener
      if (!names.includes(resolvedListener)) names.push(resolvedListener)
    }
    currentCue = undefined
    cueHasSpeech = false
    currentListener = undefined
    cueHasReaction = false
  }

  for (const raw of fountain.split(/\r?\n/)) {
    const line = raw.trim()
    if (inRegularNote) {
      if (line.includes(']]')) inRegularNote = false
      continue
    }
    const cue = /^@(.+)$/.exec(line)
    if (cue) {
      flush()
      currentCue = cueSpeakerName(cue[1])
      continue
    }
    if (isListenerNoteCandidate(line)) {
      currentListener = listenerFromNote(line)
      continue
    }
    if (isFountainNote(line)) {
      if (!line.includes(']]')) inRegularNote = true
      continue
    }
    if (line === '' || isSceneHeading(line) || isTitlePageLine(line)) {
      if (line === '' && currentCue && !cueHasSpeech) continue
      flush()
      continue
    }
    if (currentCue && !isPerformanceDirection(line)) {
      cueHasSpeech = true
      if (currentListener && /\|[^|]+\|/.test(line)) cueHasReaction = true
    }
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
  let currentListener: string | undefined
  let currentCueHasDialogue = false
  let inRegularNote = false

  for (const raw of lines) {
    const line = raw.trim()
    if (inRegularNote) {
      if (line.includes(']]')) inRegularNote = false
      continue
    }
    if (line === '') {
      // Fountain commonly puts a blank line between a cue, its parenthetical,
      // and the spoken line. Keep that cue alive until a spoken line has been
      // consumed; after a dialogue block, the blank line returns to narration.
      if (currentCueHasDialogue) {
        currentSpeaker = undefined
        currentCueHasDialogue = false
        currentListener = undefined
      }
      continue
    }
    if (isListenerNoteCandidate(line)) {
      currentListener = listenerFromNote(line)
      continue
    }
    if (isFountainNote(line)) {
      if (!line.includes(']]')) inRegularNote = true
      continue
    }
    const cue = /^@(.+)$/.exec(line)
    if (cue) {
      currentSpeaker = cueSpeakerName(cue[1])
      currentListener = undefined
      currentCueHasDialogue = false
      continue
    }
    if (isSceneHeading(line)) {
      if (wantNarrator) {
        const heading = spokenSceneHeading({ text: raw, start: 0, end: raw.length })
        if (heading) return firstSentence(heading.text)
      }
      currentSpeaker = undefined
      currentListener = undefined
      currentCueHasDialogue = false
      continue
    }
    if (isTitlePageLine(line)) {
      currentSpeaker = undefined
      currentListener = undefined
      currentCueHasDialogue = false
      continue
    }
    if (isPerformanceDirection(line)) continue
    const projection = listenerSpeech(line, currentListener)
    if (wantNarrator) {
      if (currentSpeaker === undefined || currentSpeaker === narratorSpeaker || isNarratorSpeaker(currentSpeaker)) {
        currentCueHasDialogue = currentSpeaker !== undefined
        return firstSentence(projection.main.replace(/\s+/g, ' '))
      }
      currentCueHasDialogue = true
      continue
    }
    if (
      currentSpeaker !== undefined &&
      normalizeSpeakerAlias(currentSpeaker) === normalizeSpeakerAlias(speaker)
    ) {
      currentCueHasDialogue = true
      if (projection.main.trim()) return firstSentence(projection.main.replace(/\s+/g, ' '))
      continue
    }
    if (
      currentListener !== undefined &&
      normalizeSpeakerAlias(currentListener) === normalizeSpeakerAlias(speaker) &&
      projection.reactions[0]
    ) {
      currentCueHasDialogue = true
      return firstSentence(projection.reactions[0])
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
