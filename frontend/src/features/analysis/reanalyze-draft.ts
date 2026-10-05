import type { Character, DocumentDetail } from '@/api/contracts'

type ReanalysisArc = DocumentDetail['emotion_arc']

function emptyCharacter(name: string): Character {
  return {
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
  }
}

/** Merge a reanalysis result while retaining registered characters absent from the current script. */
export function applyReanalysisArc(document: DocumentDetail, result: ReanalysisArc): Pick<DocumentDetail, 'analysis' | 'emotion_arc'> {
  const characters = [...document.analysis.characters]
  for (const name of Object.keys(result.characters)) {
    if (!characters.some((character) => character.name === name)) characters.push(emptyCharacter(name))
  }

  return {
    analysis: { ...document.analysis, characters },
    emotion_arc: {
      ...result,
      characters: { ...document.emotion_arc.characters, ...result.characters },
    },
  }
}
