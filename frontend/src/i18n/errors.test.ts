import { describe, expect, it } from 'vitest'

import { ApiClientError } from '@/api/client'
import { createNarravantI18n } from './i18n'
import { localizeUiError, toUiError } from './errors'

function translate(locale: 'en' | 'ja') {
  return createNarravantI18n(locale).getFixedT(locale, 'errors')
}

describe('UI error localization', () => {
  it('uses an API error code and never renders the backend message', () => {
    const descriptor = toUiError(
      new ApiClientError(404, 'DOCUMENT_NOT_FOUND', 'BACKEND_SENTINEL_DO_NOT_RENDER'),
      'HTTP_ERROR',
    )
    const localized = localizeUiError(translate('en'), descriptor)

    expect(localized.title).toBe('Document unavailable')
    expect(localized.message).toBe('This document is no longer available.')
    expect(JSON.stringify(localized)).not.toContain('BACKEND_SENTINEL_DO_NOT_RENDER')
  })

  it('uses retryable Import guidance and ignores transport message text', () => {
    const localized = localizeUiError(translate('ja'), {
      code: 'IMPORT_FAILED', retryable: true,
    })

    expect(localized.message).toContain('同じファイル')
  })

  it('renders unknown codes and only exposes INTERNAL_ERROR correlation id', () => {
    const unknown = localizeUiError(translate('en'), { code: 'FUTURE_CODE' })
    const internal = localizeUiError(translate('ja'), {
      code: 'INTERNAL_ERROR',
      context: { correlation_id: 'ref-42', document_id: 'do-not-display' },
    })

    expect(unknown.message).toBe('An unexpected error occurred. (Code: FUTURE_CODE)')
    expect(internal.reference).toBe('参照番号: ref-42')
    expect(JSON.stringify(internal)).not.toContain('do-not-display')
  })

  it('uses the caller supplied frontend fallback for non-code errors', () => {
    expect(toUiError(new Error('BACKEND_SENTINEL_DO_NOT_RENDER'), 'SAVE_FAILED')).toEqual({ code: 'SAVE_FAILED' })
  })
})
