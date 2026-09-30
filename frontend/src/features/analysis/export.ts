import type { DocumentDetail } from '@/api/contracts'
import { countSceneHeadings } from './fountain-scenes'

export type NativeIdentifiedTurningPoint = {
  tp_number: number
  availability: 'identified'
  scene_number: number
  change: string
  involved_characters: Array<{
    name: string
    goal: string
    conflict: string
    choice: string
    action: string
    change: string
  }>
  reason: null
}

export type NativeNotApplicableTurningPoint = {
  tp_number: number
  availability: 'not_applicable'
  scene_number: null
  change: null
  involved_characters: []
  reason: string
}

export type NativeTurningPoint = NativeIdentifiedTurningPoint | NativeNotApplicableTurningPoint

export type NativeExchange = {
  format: 'narravant-native'
  format_version: 1
  exported_at: string
  document: {
    title: string
    source_fountain: string
    metadata: { title: string; logline: string; synopsis: string; theme_setting: string }
    analysis: {
      status: 'completed'
      turning_points: NativeTurningPoint[]
      characters: Array<{
        name: string
        external_goal?: string | null
        internal_need?: string | null
        fear_or_cost?: string | null
        obstacle?: string | null
        choice?: string | null
        agency?: string | null
        goal_to_outcome?: string | null
        related_turning_points: number[]
        voice_traits?: string
      }>
    }
    emotion_arc: { valence: number[]; tension: number[]; characters: Record<string, number[]> }
    narrator?: { voice_traits: string }
    voice_assignments?: Array<{ speaker: string; voice_id?: string | null; voice_traits: string }>
  }
}

function requiredMetadata(metadata: DocumentDetail['metadata'], key: 'logline' | 'synopsis' | 'theme_setting'): string {
  const value = metadata[key]
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Native JSON Export requires metadata.${key}.`)
  return value
}

/** True only when the editable Fountain still agrees with the completed analysis arrays. */
export function canExportNative(document: Pick<DocumentDetail, 'title' | 'source_fountain' | 'analysis' | 'emotion_arc' | 'scenes'>): boolean {
  if (document.analysis.status !== 'completed') return false
  const fountainTitle = /^Title:\s*(.+)$/im.exec(document.source_fountain)?.[1]?.trim()
  if (!fountainTitle || fountainTitle.normalize('NFC') !== document.title.trim().normalize('NFC')) return false
  const sceneCount = countSceneHeadings(document.source_fountain)
  if (sceneCount === 0 || sceneCount !== document.scenes.length) return false
  const mapping = document.emotion_arc.scene_mapping
  if (!hasDeterministicSceneMapping(mapping, sceneCount)) return false
  const expectedArcLength = mapping.length
  if (document.emotion_arc.valence.length !== expectedArcLength || document.emotion_arc.tension.length !== expectedArcLength) return false
  const names = new Set(document.analysis.characters.map((character) => character.name))
  if (names.size !== document.analysis.characters.length || names.size !== Object.keys(document.emotion_arc.characters).length) return false
  if (!Object.entries(document.emotion_arc.characters).every(([name, values]) => names.has(name) && values.length === expectedArcLength)) return false

  const tps = document.analysis.turning_points
  if (!tps || tps.length !== 5) return false
  const tpNumbers = tps.map((tp) => tp.tp_number).sort((a, b) => a - b)
  if (!tpNumbers.every((num, idx) => num === idx + 1)) return false

  return tps.every((tp) => {
    if (tp.availability === 'identified') {
      return (
        typeof tp.scene_number === 'number' &&
        tp.scene_number >= 1 &&
        tp.scene_number <= sceneCount &&
        typeof tp.change === 'string' &&
        tp.change.trim().length > 0 &&
        Array.isArray(tp.involved_characters) &&
        tp.involved_characters.length >= 1 &&
        tp.involved_characters.every(
          (c) => c.name.trim() && c.goal.trim() && c.conflict.trim() && c.choice.trim() && c.action.trim() && c.change.trim(),
        ) &&
        (tp.reason === null || tp.reason === undefined)
      )
    }
    if (tp.availability === 'not_applicable') {
      return (
        (tp.scene_number === null || tp.scene_number === undefined) &&
        (tp.change === null || tp.change === undefined) &&
        (!tp.involved_characters || tp.involved_characters.length === 0) &&
        typeof tp.reason === 'string' &&
        tp.reason.trim().length > 0
      )
    }
    return false
  })
}

function hasDeterministicSceneMapping(mapping: DocumentDetail['emotion_arc']['scene_mapping'], sceneCount: number): boolean {
  if (!Array.isArray(mapping) || mapping.length === 0) return false
  const pointCount = mapping.length
  return mapping.every((point, index) => {
    const startSceneNumber = Math.floor(index * sceneCount / pointCount) + 1
    const endSceneNumber = Math.floor((index + 1) * sceneCount / pointCount)
    const representativeSceneNumber = startSceneNumber + Math.floor((endSceneNumber - startSceneNumber) / 2)
    return point.point_number === index + 1 &&
      point.start_scene_number === startSceneNumber &&
      point.end_scene_number === endSceneNumber &&
      point.representative_scene_number === representativeSceneNumber
  })
}

/** Serialize only the documented interchange fields; IDs, locks, GCS and capabilities never enter this object. */
export function serializeNativeJson(
  document: Pick<DocumentDetail, 'title' | 'source_fountain' | 'metadata' | 'analysis' | 'emotion_arc'> & {
    narrator?: DocumentDetail['narrator']
    voice_assignments?: DocumentDetail['voice_assignments']
  },
  exportedAt = new Date().toISOString()
): NativeExchange {
  if (document.analysis.status !== 'completed') throw new Error('Native JSON Export requires completed analysis.')
  return {
    format: 'narravant-native',
    format_version: 1,
    exported_at: exportedAt,
    document: {
      title: document.title,
      source_fountain: document.source_fountain,
      metadata: {
        // The external title triad is intentionally normalized from the visible title.
        title: document.title,
        logline: requiredMetadata(document.metadata, 'logline'),
        synopsis: requiredMetadata(document.metadata, 'synopsis'),
        theme_setting: requiredMetadata(document.metadata, 'theme_setting'),
      },
      analysis: {
        status: 'completed',
        turning_points: document.analysis.turning_points.map((point): NativeTurningPoint => {
          if (point.availability === 'identified') {
            return {
              tp_number: point.tp_number,
              availability: 'identified',
              scene_number: point.scene_number!,
              change: point.change!,
              involved_characters: point.involved_characters.map((character) => ({
                name: character.name,
                goal: character.goal,
                conflict: character.conflict,
                choice: character.choice,
                action: character.action,
                change: character.change,
              })),
              reason: null,
            }
          }
          return {
            tp_number: point.tp_number,
            availability: 'not_applicable',
            scene_number: null,
            change: null,
            involved_characters: [],
            reason: point.reason!,
          }
        }),
        characters: document.analysis.characters.map((character) => ({
          name: character.name,
          external_goal: character.external_goal,
          internal_need: character.internal_need,
          fear_or_cost: character.fear_or_cost,
          obstacle: character.obstacle,
          choice: character.choice,
          agency: character.agency,
          goal_to_outcome: character.goal_to_outcome,
          related_turning_points: [...(character.related_turning_points ?? [])],
          voice_traits: character.voice_traits ?? '',
        })),
      },
      emotion_arc: {
        valence: [...document.emotion_arc.valence],
        tension: [...document.emotion_arc.tension],
        characters: Object.fromEntries(Object.entries(document.emotion_arc.characters).map(([name, values]) => [name, [...values]])),
      },
      narrator: {
        voice_traits: document.narrator?.voice_traits ?? '',
      },
      voice_assignments: (document.voice_assignments ?? []).map((va) => ({
        speaker: va.speaker,
        voice_id: va.voice_id ?? null,
        voice_traits: va.voice_traits ?? '',
      })),
    },
  }
}

export function exportFilename(title: string, format: 'fountain' | 'json'): string {
  const sanitized = title.trim().replaceAll(/[^A-Za-z0-9_\-\u3005\u3040-\u30ff\u3400-\u9fff]/g, '_')
  const base = sanitized.replaceAll('_', '') ? sanitized : 'narravant-export'
  return `${base}.${format}`
}
