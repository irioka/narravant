import { describe, expect, it } from 'vitest'

import { countSceneHeadings, firstUtteranceLine, hasSceneHeadings, narratorSpeakerName, playbackStartAtOffset, sceneHeadingRange, spokenCharacterNames, utteranceRange } from './fountain-scenes'

const NARRATOR = 'ナレーター'

describe('narrator speaker identity', () => {
  it.each(['Narrator', 'NARRATOR', 'ナレーター'])('明示された %s を本文の文字種より優先する', (speaker) => {
    const source = `Title: 日本語のタイトル\n\nINT. ROOM - DAY\n\n@${speaker}\nHello.`
    expect(narratorSpeakerName(source)).toBe(speaker)
    expect(firstUtteranceLine(source, 'ナレーター', 'ナレーター')).toBe('ROOM - DAY')
  })
})

const FOUNTAIN = [
  'Title: 走れメロス',
  '',
  'EXT. シラクスの市街 - 昼 #1#',
  '',
  '南欧の陽光が降り注ぐ街並み。異様な静けさが漂う。',
  '',
  '@メロス',
  '何かがおかしい。まるで町全体が死に絶えたかのようだ。',
  '',
  '@ディオニス',
  'この短刀で何をする気だったのか。言え。',
].join('\n')

describe('Fountain scene headings', () => {
  it('canonical scene heading prefixesを大文字小文字を問わずCRLFでも数える', () => {
    const source = 'int. ROOM - DAY #1#\r\nEXT. ROAD - NIGHT #2#\r\nEST. CITY - DAY #3#\r\nINT./EXT. CAR - DAY #4#\r\nINT/EXT HOUSE - DAY #5#\r\nI/E. TRAIN - DAY #6#'
    expect(countSceneHeadings(source)).toBe(6)
    expect(hasSceneHeadings(source)).toBe(true)
  })

  it('台詞・ト書きをシーン見出しとして誤認しない', () => {
    const source = '@INT. ACTOR\nThe INT. appears in dialogue.\n!EXT. forced action\nTHE EXTERIOR is quiet.'
    expect(countSceneHeadings(source)).toBe(0)
    expect(hasSceneHeadings(source)).toBe(false)
  })
})

describe('firstUtteranceLine', () => {
  it('話者の最初の台詞の一文を返す', () => {
    expect(firstUtteranceLine(FOUNTAIN, 'メロス', NARRATOR)).toBe('何かがおかしい。')
    expect(firstUtteranceLine(FOUNTAIN, 'ディオニス', NARRATOR)).toBe('この短刀で何をする気だったのか。')
  })

  it('ナレーターは区分と番号を除いたシーン見出しを先に返す', () => {
    expect(firstUtteranceLine(FOUNTAIN, NARRATOR, NARRATOR)).toBe('シラクスの市街 - 昼')
  })

  it('話者 cue 後の演技指示を飛ばして最初のセリフを返す', () => {
    const withDirection = [
      'INT. ROOM - DAY',
      '',
      '@メロス',
      '(不安げに、早口で)',
      '何かがおかしい。',
    ].join('\n')

    expect(firstUtteranceLine(withDirection, 'メロス', NARRATOR)).toBe('何かがおかしい。')
  })

  it('全角括弧の演技指示と空行を飛ばしてセリフまで返す', () => {
    const withJapaneseDirection = [
      'INT. ROOM - DAY',
      '',
      '@メロス',
      '',
      '（青ざめ、銃を下ろす）',
      '',
      'ここはどこの山だ。',
    ].join('\n')

    expect(firstUtteranceLine(withJapaneseDirection, 'メロス', NARRATOR)).toBe('ここはどこの山だ。')
  })

  it('該当話者がいなければ空文字を返す', () => {
    expect(firstUtteranceLine(FOUNTAIN, '存在しない話者', NARRATOR)).toBe('')
  })
})

describe('playback source ranges', () => {
  const source = [
    'Title: 位置',
    '',
    'INT. ROOM - DAY #1#',
    '',
    'Narration.',
    '',
    '@専門の猟師',
    '（低い声で）',
    '',
    'ここは危険だ。',
    '',
    '@通行人',
    '逃げて！',
  ].join('\n')

  it('returns the spoken scene heading and ordered utterance ranges', () => {
    expect(source.slice(...Object.values(sceneHeadingRange(source, 1)!))).toBe('INT. ROOM - DAY #1#')
    expect(source.slice(...Object.values(utteranceRange(source, 1, 0)!))).toBe('ROOM - DAY')
    expect(source.slice(...Object.values(utteranceRange(source, 1, 1)!))).toBe('Narration.')
    expect(source.slice(...Object.values(utteranceRange(source, 1, 2)!))).toBe('ここは危険だ。')
    expect(source.slice(...Object.values(utteranceRange(source, 1, 3)!))).toBe('逃げて！')
  })

  it('collects every spoken non-narrator cue', () => {
    expect(spokenCharacterNames(source)).toEqual(['専門の猟師', '通行人'])
  })

  it('keeps a full-width parenthetical alias in the cue name (matches backend parser)', () => {
    // The backend FountainParser strips only ASCII parentheses, so the speaker
    // name on utterances and character profiles is "専門の猟師（案内人）". The
    // frontend must agree, otherwise the voice UI synthesizes a duplicate,
    // empty "専門の猟師" alongside the real profile.
    const aliased = ['Title: 別名', '', 'INT. 森 - 昼 #1#', '', '@専門の猟師（案内人）', 'ここは危険だ。'].join('\n')
    expect(spokenCharacterNames(aliased)).toEqual(['専門の猟師（案内人）'])
  })

  it('maps a cursor inside a paragraph to that paragraph playback start', () => {
    const cursor = source.indexOf('危険') + 1
    expect(playbackStartAtOffset(source, cursor)).toEqual({ sceneNumber: 1, utteranceIndex: 2 })
  })

  it('maps a cursor in a heading or blank line to the next utterance', () => {
    const headingCursor = source.indexOf('INT. ROOM')
    expect(playbackStartAtOffset(source, headingCursor)).toEqual({ sceneNumber: 1, utteranceIndex: 0 })
  })

  it('CONT\'D cues collapse to single character and preserve speech and preview', () => {
    const contdSource = [
      'INT. CABIN - DAY #1#',
      '',
      '@MAYA',
      '(quietly)',
      'First line of speech.',
      '',
      '@MAYA (CONT\'D)',
      'Second line of speech.',
    ].join('\n')
    expect(spokenCharacterNames(contdSource)).toEqual(['MAYA'])
    expect(firstUtteranceLine(contdSource, 'MAYA', NARRATOR)).toBe('First line of speech.')
  })
})
