const VALENCE_NEUTRAL = 4

export const toDisplayValence = (value: number | null | undefined): number | null =>
  value === null || value === undefined || value === 0 ? null : value - VALENCE_NEUTRAL