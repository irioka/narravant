import type { TFunction } from 'i18next';

import { localizeUiError } from '@/i18n/errors';

export interface ImportRuntimeError {
  title: string
  message: string
}

/**
 * Build the localized title/message shown when an import/analysis task fails.
 * The retryable IMPORT_FAILED case is localized through the shared error
 * catalog so it stays in sync with the active locale.
 */
export function importTaskRuntimeError(
  t: TFunction<'errors'>,
  error: { code: string; message: string; retryable: boolean },
): ImportRuntimeError {
  if (error.code === 'IMPORT_FAILED' && error.retryable) {
    const localized = localizeUiError(t, { code: 'IMPORT_FAILED', retryable: true })
    return { title: localized.title, message: localized.message }
  }
  const localized = localizeUiError(t, { code: 'IMPORT_FAILED' })
  return { title: localized.title, message: localized.message }
}
