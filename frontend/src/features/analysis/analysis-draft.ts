import type { DocumentDetail } from '@/api/contracts'
import type { ImportDraftPayload } from './import-session'
export interface AnalysisDraftState { base: DocumentDetail; value: DocumentDetail; pendingCurrentVersionId?: number; importTaskId?: string }
export function createImportedDraftState(payload: ImportDraftPayload, taskId: string, titleFallback: string, now: string): AnalysisDraftState {
  const value: DocumentDetail = {
    ...payload,
    title: payload.title.trim() || titleFallback,
    analysis: { ...payload.analysis, characters: payload.analysis.characters.map((character) => ({
      ...character, external_goal: character.external_goal ?? null, internal_need: character.internal_need ?? null,
      fear_or_cost: character.fear_or_cost ?? null, obstacle: character.obstacle ?? null, choice: character.choice ?? null,
      agency: character.agency ?? null, goal_to_outcome: character.goal_to_outcome ?? null,
    })) },
    owner_user_id: '', owner_email: '', shared_count: 0,
    created_at: now, updated_at: now, current_version_id: payload.version_id, expected_version: payload.expected_version ?? 1,
    is_saved: false, gcs_uri: null, generation: null, capabilities: { can_edit: true, can_share: false, can_delete: false },
  }
  return { base: value, value, importTaskId: taskId }
}
