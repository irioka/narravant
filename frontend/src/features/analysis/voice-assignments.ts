import type { Character, DocumentDetail } from '@/api/contracts'
import { isNarratorSpeaker, narratorSpeakerName, spokenCharacterNames } from './fountain-scenes'

export type VoiceAssignment = NonNullable<DocumentDetail['voice_assignments']>[number]

/**
 * Fallback narrator speaker name for Japanese scripts.
 * Prefer `narratorSpeakerName(document.source_fountain)` when the Fountain
 * source is available so the key matches the actual cue in the script.
 */
export const NARRATOR_SPEAKER = 'ナレーター'

/** Add spoken cues that an older analysis omitted from its character profiles. */
export function charactersWithSpokenCues(document: DocumentDetail): Character[] {
  const spokenNames = new Set(spokenCharacterNames(document.source_fountain))
  const characters = document.analysis.characters.map((character) => character)
  const existing = new Set(characters.map((character) => character.name))
  for (const name of spokenNames) {
    if (existing.has(name)) continue
    characters.push({
      name,
      external_goal: null,
      internal_need: null,
      fear_or_cost: null,
      obstacle: null,
      choice: null,
      agency: null,
      goal_to_outcome: null,
      related_turning_points: [],
      voice_traits: '',
    })
    existing.add(name)
  }
  return characters
}

/** Return the assignment for a speaker, or undefined when none exists. */
export function assignmentForSpeaker(
  assignments: VoiceAssignment[] | undefined,
  speaker: string,
): VoiceAssignment | undefined {
  const exact = assignments?.find((assignment) => assignment.speaker === speaker)
  if (exact?.voice_id || !isNarratorSpeaker(speaker)) return exact
  const aliases = assignments?.filter((assignment) => isNarratorSpeaker(assignment.speaker))
  return aliases?.find((assignment) => assignment.voice_id) ?? exact ?? aliases?.[0]
}

/** Return the created voice_id for a speaker, or undefined when it has no voice yet. */
export function voiceIdForSpeaker(
  assignments: VoiceAssignment[] | undefined,
  speaker: string,
): string | undefined {
  const voiceId = assignmentForSpeaker(assignments, speaker)?.voice_id
  return voiceId ?? undefined
}

/**
 * Insert or replace the assignment for a speaker, preserving all other entries.
 * Old voice ids are never deleted here; a new voice_id simply supersedes the
 * previous mapping (voice ids stay version-managed on the backend).
 */
export function upsertVoiceAssignment(
  assignments: VoiceAssignment[] | undefined,
  next: VoiceAssignment,
): VoiceAssignment[] {
  const list = assignments ?? []
  const matches = (assignment: VoiceAssignment) => assignment.speaker === next.speaker
    || (isNarratorSpeaker(next.speaker) && isNarratorSpeaker(assignment.speaker))
  const index = list.findIndex(matches)
  if (index === -1) return [...list, next]
  return list.flatMap((assignment, position) => position === index ? [next] : matches(assignment) ? [] : [assignment])
}

/** All speakers that need a voice: every character plus the narrator. */
export function speakersNeedingVoice(document: DocumentDetail): { speaker: string; voice_traits: string }[] {
  const narrator = narratorSpeakerName(document.source_fountain)
  const spokenNames = new Set(spokenCharacterNames(document.source_fountain))
  const characters = charactersWithSpokenCues(document).filter((character) => spokenNames.has(character.name)).map((character) => ({
    speaker: character.name,
    voice_traits: character.voice_traits ?? '',
  }))
  return [
    { speaker: narrator, voice_traits: document.narrator?.voice_traits ?? '' },
    ...characters,
  ]
}

/**
 * Distinct speakers that appear in the script and still lack a voice_id.
 * Used to block playback and list every missing speaker at once.
 */
export function missingVoiceSpeakers(document: DocumentDetail): string[] {
  const assignments = document.voice_assignments ?? []
  const missing: string[] = []
  for (const { speaker } of speakersNeedingVoice(document)) {
    if (!voiceIdForSpeaker(assignments, speaker) && !missing.includes(speaker)) {
      missing.push(speaker)
    }
  }
  return missing
}
