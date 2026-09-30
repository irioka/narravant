import { taskProgressEventSchema } from '@/api/generated/task-events'
import { describe, expect, it } from 'vitest'
import { type DisplayProgressPhase, progressPhaseLabel } from './progress-phase'

describe('progressPhaseLabel', () => {
  const schemaPhases = taskProgressEventSchema.shape.phase.options

  it('maps every generated TaskProgressPhase to the English feature catalog', () => {
    expect(schemaPhases).toEqual([
      'queued',
      'cancelling',
      'structuring',
      'classifying',
      'reading',
      'building_plot_graph',
      'planning',
      'writing',
      'verifying',
      'analyzing',
    ])
    expect(schemaPhases.map(progressPhaseLabel)).toEqual([
      'Queued',
      'Cancelling…',
      'Validating Native JSON…',
      'Classifying input…',
      'Reading source…',
      'Building causal plot graph…',
      'Planning adaptation outline…',
      'Writing scenes…',
      'Verifying consistency…',
      'Analyzing structure and emotional arc…',
    ])
  })

  it('maps client-only terminal phases', () => {
    expect(progressPhaseLabel('completed')).toBe('Completed')
    expect(progressPhaseLabel('cancelled')).toBe('Cancelled')
  })

  it('rejects unknown phases instead of falling back', () => {
    expect(() => progressPhaseLabel('unknown_phase' as DisplayProgressPhase)).toThrow(
      'Unknown progress phase: unknown_phase',
    )
  })
})
