import { taskProgressEventSchema } from '@/api/generated/task-events'
import type { z } from 'zod'

export type TaskProgressPhase = z.infer<typeof taskProgressEventSchema>['phase']
export type DisplayProgressPhase = TaskProgressPhase | 'completed' | 'cancelled'

const PROGRESS_PHASE_LABELS_EN = {
  queued: 'Queued',
  cancelling: 'Cancelling…',
  structuring: 'Validating Native JSON…',
  classifying: 'Classifying input…',
  reading: 'Reading source…',
  building_plot_graph: 'Building causal plot graph…',
  planning: 'Planning adaptation outline…',
  writing: 'Writing scenes…',
  verifying: 'Verifying consistency…',
  analyzing: 'Analyzing structure and emotional arc…',
  completed: 'Completed',
  cancelled: 'Cancelled',
} satisfies Record<DisplayProgressPhase, string>

export function progressPhaseLabel(phase: DisplayProgressPhase): string {
  const label = PROGRESS_PHASE_LABELS_EN[phase]
  if (!label) {
    throw new Error(`Unknown progress phase: ${String(phase)}`)
  }
  return label
}
