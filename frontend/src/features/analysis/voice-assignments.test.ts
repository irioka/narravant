import { describe, expect, it } from 'vitest'

import type { DocumentDetail } from '@/api/contracts'
import { missingVoiceSpeakers, upsertVoiceAssignment, voiceIdForSpeaker } from './voice-assignments'

describe('narrator voice compatibility', () => {
  it.each(['Narrator', 'NARRATOR', 'ナレーター'])('既存の日本語割当を %s の声として使う', (speaker) => {
    const assignments = [{ speaker: 'ナレーター', voice_id: 'voices/synthetic', voice_traits: '' }]
    expect(voiceIdForSpeaker(assignments, speaker)).toBe('voices/synthetic')
    const document = {
      source_fountain: `INT. ROOM - DAY\n\n@${speaker}\nHello.`,
      narrator: { voice_traits: 'Calm' }, analysis: { characters: [] }, voice_assignments: assignments,
    } as unknown as DocumentDetail
    expect(missingVoiceSpeakers(document)).toEqual([])
  })

  it('日本語原稿でも英語名で生成済みの声を利用する', () => {
    expect(voiceIdForSpeaker([{ speaker: 'Narrator', voice_id: 'voices/synthetic', voice_traits: '' }], 'ナレーター')).toBe('voices/synthetic')
  })

  it('ナレーターを再生成したとき古い別名割当を置き換える', () => {
    const character = { speaker: 'Alice', voice_id: 'voices/alice', voice_traits: '' }
    const next = { speaker: 'Narrator', voice_id: 'voices/new', voice_traits: '' }
    expect(upsertVoiceAssignment([{ speaker: 'ナレーター', voice_id: 'voices/old', voice_traits: '' }, character], next)).toEqual([next, character])
  })
})
