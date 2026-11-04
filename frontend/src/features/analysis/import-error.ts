import type { TFunction } from 'i18next'

import { localizeUiError } from '@/i18n/errors'

export interface ImportRuntimeError {
  title: string
  message: string
}

const stopReasons = {
  analysis_output_language: 'adaptation.reasons.analysisLanguage',
  analysis_invalid_title: 'adaptation.reasons.analysisTitle',
  analysis_valence_length: 'adaptation.reasons.analysisValenceLength',
  analysis_valence_scale: 'adaptation.reasons.analysisValenceScale',
  analysis_tension_length: 'adaptation.reasons.analysisTensionLength',
  analysis_tension_scale: 'adaptation.reasons.analysisTensionScale',
  analysis_character_arc: 'adaptation.reasons.analysisCharacterArc',
  analysis_character_profiles: 'adaptation.reasons.analysisCharacterProfiles',
  analysis_turning_points: 'adaptation.reasons.analysisTurningPoints',
  analysis_related_turning_points: 'adaptation.reasons.analysisRelatedTurningPoints',
  analysis_theme_citation: 'adaptation.reasons.analysisThemeCitation',
  analysis_required_schema_fields: 'adaptation.reasons.analysisSchema',
  plan_schema: 'adaptation.reasons.planSchema',
  plan_scene_sequence: 'adaptation.reasons.planSceneSequence',
  plan_characters: 'adaptation.reasons.planCharacters',
  plan_event_reference: 'adaptation.reasons.planEventReference',
  plan_presentation_order: 'adaptation.reasons.planPresentationOrder',
  plan_event_coverage: 'adaptation.reasons.planEventCoverage',
  plan_source_coverage: 'adaptation.reasons.planSourceCoverage',
  plan_dependency: 'adaptation.reasons.planDependency',
  revision_budget_exhausted: 'adaptation.reasons.revisionBudgetExhausted',
  major_issues_after_revision: 'adaptation.reasons.majorIssuesAfterRevision',
  major_issues_in_final_verification: 'adaptation.reasons.majorIssuesInFinalVerification',
  scene_structure_invalid: 'adaptation.reasons.sceneStructureInvalid',
  invalid_verification_response: 'adaptation.reasons.invalidVerificationResponse',
  invalid_verification_reference: 'adaptation.reasons.invalidVerificationReference',
  output_language_mismatch: 'adaptation.reasons.outputLanguageMismatch',
} as const

const issueTypes = {
  event_omission: 'adaptation.issues.eventOmission',
  motivation_change: 'adaptation.issues.motivationChange',
  knowledge_boundary: 'adaptation.issues.knowledgeBoundary',
  speaker_attribution: 'adaptation.issues.speakerAttribution',
  ending_change: 'adaptation.issues.endingChange',
  foreshadowing_mismatch: 'adaptation.issues.foreshadowingMismatch',
  source_contradiction: 'adaptation.issues.sourceContradiction',
  fountain_invalid: 'adaptation.issues.fountainInvalid',
} as const

function nonnegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

/** Translate known server reasons without displaying source text or free-form model output. */
export function importTaskRuntimeError(
  t: TFunction<'errors'>,
  error: { code: string; message: string; retryable: boolean; context?: Readonly<Record<string, unknown>> | null },
): ImportRuntimeError {
  const localized = localizeUiError(t, { ...error, context: error.context ?? undefined })
  const context = error.context
  const reason = context?.reason
  if (typeof reason !== 'string' || !Object.hasOwn(stopReasons, reason)) return localized

  const details = [localized.message, t(stopReasons[reason as keyof typeof stopReasons])]
  if (nonnegativeInteger(context?.scene_number) && context.scene_number > 0) {
    details.push(t('adaptation.scene', { sceneNumber: context.scene_number }))
  }
  if (reason.startsWith('plan_') && context?.phase === 'planning'
    && nonnegativeInteger(context.attempt_count) && context.attempt_count > 0
    && nonnegativeInteger(context.max_attempts) && context.max_attempts >= context.attempt_count) {
    details.push(t('adaptation.planningAttempts', { attemptCount: context.attempt_count, maxAttempts: context.max_attempts }))
  }
  if (reason.startsWith('analysis_') && context?.phase === 'analyzing') {
    if (nonnegativeInteger(context.attempt_count) && context.attempt_count > 0
      && nonnegativeInteger(context.max_attempts) && context.max_attempts >= context.attempt_count) {
      details.push(t('adaptation.analysisAttempts', { attemptCount: context.attempt_count, maxAttempts: context.max_attempts }))
    }
    if (nonnegativeInteger(context.expected_scene_count) && context.expected_scene_count > 0) {
      details.push(t('adaptation.analysisScenes', { sceneCount: context.expected_scene_count }))
    }
  }
  if (nonnegativeInteger(context?.revision_count) && nonnegativeInteger(context?.max_retries)) {
    details.push(t('adaptation.retries', { retryCount: context.revision_count, maxRetries: context.max_retries }))
  }
  if (Array.isArray(context?.issue_types)) {
    const issues = [...new Set(context.issue_types)]
      .filter((issue): issue is keyof typeof issueTypes => typeof issue === 'string' && Object.hasOwn(issueTypes, issue))
      .map((issue) => t(issueTypes[issue]))
    if (issues.length) details.push(t('adaptation.issueList', { issues: issues.join(' / ') }))
  }
  return { title: localized.title, message: details.join(' ') }
}
