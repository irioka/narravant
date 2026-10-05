import { describe, expect, it } from 'vitest'

import type { DocumentDetail } from '@/api/contracts'
import { applyReanalysisArc } from './reanalyze-draft'

const document = {
  analysis: {
    status: 'completed',
    characters: [
      { name: 'Script A', external_goal: 'keep this profile', related_turning_points: [1] },
      { name: 'Absent B', external_goal: 'keep this too', related_turning_points: [2] },
      { name: 'Added C', external_goal: null, related_turning_points: [] },
    ],
    turning_points: [{ tp_number: 1, label: 'Opportunity', availability: 'identified', scene_number: 1, change: 'preserve', involved_characters: [{ name: 'Historic', goal: 'g', conflict: 'c', choice: 'c', action: 'a', change: 'preserve' }] }],
  },
  emotion_arc: {
    valence: [4],
    tension: [1],
    characters: { 'Script A': [2], 'Absent B': [6], 'Added C': [0] },
    scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
    valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
  },
} as unknown as DocumentDetail

describe('applyReanalysisArc', () => {
  it('adds unregistered result speakers and updates only their series while keeping other histories', () => {
    const turningPoints = document.analysis.turning_points
    const result = {
      valence: [6],
      tension: [-1],
      characters: { 'Script A': [5], 'Added C': [4], 'Unregistered D': [2] },
      scene_mapping: document.emotion_arc.scene_mapping,
      valence_vector: [1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    }

    const applied = applyReanalysisArc(document, result)

    expect(applied.analysis.characters.map((character) => character.name)).toEqual(['Script A', 'Absent B', 'Added C', 'Unregistered D'])
    expect(applied.analysis.characters[0].external_goal).toBe('keep this profile')
    expect(applied.analysis.characters[1].external_goal).toBe('keep this too')
    expect(applied.analysis.characters[3]).toMatchObject({ name: 'Unregistered D', voice_traits: '', related_turning_points: [] })
    expect(applied.emotion_arc.characters).toEqual({ 'Script A': [5], 'Absent B': [6], 'Added C': [4], 'Unregistered D': [2] })
    expect(applied.emotion_arc.valence).toEqual([6])
    expect(applied.analysis.turning_points).toBe(turningPoints)
  })
})
