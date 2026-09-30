import { describe, expect, it } from 'vitest'

import type { DocumentDetail } from '@/api/contracts'
import { canExportNative, exportFilename, serializeNativeJson } from './export'

const document = {
  document_id: 'internal-document-id',
  owner_user_id: 'internal-owner-id',
  expected_version: 9,
  capabilities: { can_edit: true, can_share: true, can_delete: true },
  title: '物語 / test',
  source_fountain: 'Title: 物語 / test\n\nINT. ROOM - DAY #1#\n\nAction.\n',
  metadata: { title: '物語 / test', logline: 'Logline', synopsis: 'Synopsis', theme_setting: 'Theme' },
  analysis: {
    status: 'completed',
    turning_points: [
      {
        tp_number: 1,
        label: 'Opportunity',
        availability: 'identified',
        scene_number: 1,
        change: 'Change 1',
        involved_characters: [{ name: 'A', goal: 'g', conflict: 'c', choice: 'q', action: 'a', change: 'd' }],
        reason: null,
      },
      {
        tp_number: 2,
        label: 'Change of Plans',
        availability: 'not_applicable',
        scene_number: null,
        change: null,
        involved_characters: [],
        reason: '原作内に計画変更の転換点は確認できない。',
      },
      {
        tp_number: 3,
        label: 'Point of No Return',
        availability: 'identified',
        scene_number: 1,
        change: 'Change 3',
        involved_characters: [{ name: 'A', goal: 'g', conflict: 'c', choice: 'q', action: 'a', change: 'd' }],
        reason: null,
      },
      {
        tp_number: 4,
        label: 'Major Setback',
        availability: 'not_applicable',
        scene_number: null,
        change: null,
        involved_characters: [],
        reason: '該当なし',
      },
      {
        tp_number: 5,
        label: 'Climax',
        availability: 'identified',
        scene_number: 1,
        change: 'Change 5',
        involved_characters: [{ name: 'A', goal: 'g', conflict: 'c', choice: 'q', action: 'a', change: 'd' }],
        reason: null,
      },
    ],
    characters: [{ name: 'A', external_goal: 'Goal', internal_need: 'Need', fear_or_cost: 'Cost', obstacle: 'Obstacle', choice: 'Choice', agency: 'Agency', goal_to_outcome: 'Outcome', related_turning_points: [1], voice_traits: '落ち着いた低音' }],
  },
  narrator: { voice_traits: '知的な語り手' },
  voice_assignments: [
    { speaker: 'A', voice_id: 'voices/voice-a', voice_traits: '落ち着いた低音' },
    { speaker: 'ナレーター', voice_id: 'voices/voice-narrator', voice_traits: '知的な語り手' },
  ],
  emotion_arc: { valence: [4], tension: [0], characters: { A: [4] }, scene_mapping: oneToOneMapping(1), valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1] },
  scenes: [{ scene_number: 1, heading: 'INT. ROOM - DAY', text: 'INT. ROOM - DAY #1#\n\nAction.', dialogues: [] }],
} as unknown as DocumentDetail

describe('Native JSON export', () => {
  it('serializes only the interchange allowlist and supports Import round-trip fields', () => {
    const exported = serializeNativeJson(document, '2026-09-12T12:34:56Z')

    expect(exported).toEqual(expect.objectContaining({ format: 'narravant-native', format_version: 1, exported_at: '2026-09-12T12:34:56Z' }))
    expect(exported.document).toEqual(expect.objectContaining({ title: document.title, source_fountain: document.source_fountain }))
    expect(JSON.stringify(exported)).not.toMatch(/document_id|owner_user_id|expected_version|capabilities|valence_vector|Opportunity|Change of Plans/)

    expect(exported.document.analysis.characters[0].voice_traits).toBe('落ち着いた低音')
    expect(exported.document.narrator).toEqual({ voice_traits: '知的な語り手' })
    expect(exported.document.voice_assignments).toEqual([
      { speaker: 'A', voice_id: 'voices/voice-a', voice_traits: '落ち着いた低音' },
      { speaker: 'ナレーター', voice_id: 'voices/voice-narrator', voice_traits: '知的な語り手' },
    ])

    const tps = exported.document.analysis.turning_points
    expect(tps).toHaveLength(5)
    expect(tps[0]).toEqual({
      tp_number: 1,
      availability: 'identified',
      scene_number: 1,
      change: 'Change 1',
      involved_characters: [{ name: 'A', goal: 'g', conflict: 'c', choice: 'q', action: 'a', change: 'd' }],
      reason: null,
    })
    expect(tps[1]).toEqual({
      tp_number: 2,
      availability: 'not_applicable',
      scene_number: null,
      change: null,
      involved_characters: [],
      reason: '原作内に計画変更の転換点は確認できない。',
    })
  })

  it('blocks stale Native JSON while always allowing Fountain', () => {
    expect(canExportNative(document)).toBe(true)
    expect(canExportNative({ ...document, source_fountain: `${document.source_fountain}\n\nEXT. STREET - NIGHT #2#\n\nMore.` })).toBe(false)
    expect(canExportNative({ ...document, title: 'Different' })).toBe(false)
  })

  it('allows Native JSON export when 100 source scenes are represented by 36 mapped points', () => {
    const scenes = Array.from({ length: 100 }, (_, index) => ({ scene_number: index + 1, heading: `INT. ${index + 1}`, text: '', dialogues: [] }))
    const mapping = Array.from({ length: 36 }, (_, index) => ({
      point_number: index + 1,
      start_scene_number: Math.floor(index * 100 / 36) + 1,
      end_scene_number: Math.floor((index + 1) * 100 / 36),
      representative_scene_number: Math.floor(index * 100 / 36) + 1 + Math.floor((Math.floor((index + 1) * 100 / 36) - (Math.floor(index * 100 / 36) + 1)) / 2),
    }))
    const longDocument = {
      ...document,
      source_fountain: `Title: ${document.title}\n\n${scenes.map((scene) => `${scene.heading} #${scene.scene_number}#\n\nAction.`).join('\n\n')}`,
      scenes,
      emotion_arc: {
        ...document.emotion_arc,
        valence: Array(36).fill(4),
        tension: Array(36).fill(0),
        characters: { A: Array(36).fill(4) },
        scene_mapping: mapping,
      },
    }

    expect(canExportNative(longDocument)).toBe(true)
  })

  it('rejects documents with invalid TP numbers, counts, or broken invariants', () => {
    // Fewer than 5 TPs
    const fewerTps = {
      ...document,
      analysis: {
        ...document.analysis,
        turning_points: document.analysis.turning_points.slice(0, 4),
      },
    }
    expect(canExportNative(fewerTps)).toBe(false)

    // Duplicate TP numbers
    const duplicateTpNumber = {
      ...document,
      analysis: {
        ...document.analysis,
        turning_points: [
          ...document.analysis.turning_points.slice(0, 4),
          { ...document.analysis.turning_points[0], tp_number: 4 },
        ],
      },
    }
    expect(canExportNative(duplicateTpNumber)).toBe(false)

    // not_applicable with scene_number
    const invalidNotApplicable = {
      ...document,
      analysis: {
        ...document.analysis,
        turning_points: document.analysis.turning_points.map((tp) =>
          tp.tp_number === 2 ? { ...tp, scene_number: 1 } : tp
        ),
      },
    }
    expect(canExportNative(invalidNotApplicable as any)).toBe(false)

    // not_applicable with empty reason
    const emptyReason = {
      ...document,
      analysis: {
        ...document.analysis,
        turning_points: document.analysis.turning_points.map((tp) =>
          tp.tp_number === 2 ? { ...tp, reason: '   ' } : tp
        ),
      },
    }
    expect(canExportNative(emptyReason as any)).toBe(false)

    // identified with empty involved_characters
    const emptyInvolved = {
      ...document,
      analysis: {
        ...document.analysis,
        turning_points: document.analysis.turning_points.map((tp) =>
          tp.tp_number === 1 ? { ...tp, involved_characters: [] } : tp
        ),
      },
    }
    expect(canExportNative(emptyInvolved as any)).toBe(false)
  })

  it('sanitizes portable filenames without losing supported Japanese characters', () => {
    expect(exportFilename('物語 / test', 'json')).toBe('物語___test.json')
    expect(exportFilename('///', 'fountain')).toBe('narravant-export.fountain')
  })
})

function oneToOneMapping(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    point_number: index + 1,
    start_scene_number: index + 1,
    end_scene_number: index + 1,
    representative_scene_number: index + 1,
  }))
}
