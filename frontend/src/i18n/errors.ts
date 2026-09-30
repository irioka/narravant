import type { TFunction } from 'i18next'

import { ApiClientError } from '@/api/client'
import { API_ERROR_CODES, FRONTEND_ERROR_CODES, SSE_ERROR_CODES } from './catalog'

export type FrontendErrorCode = (typeof FRONTEND_ERROR_CODES)[number]
export type KnownErrorCode = (typeof API_ERROR_CODES)[number] | (typeof SSE_ERROR_CODES)[number] | FrontendErrorCode

export interface UiErrorDescriptor {
  code: string
  retryable?: boolean
  context?: Readonly<Record<string, unknown>>
}

export interface LocalizedUiError {
  title: string
  message: string
  reference?: string
}

const titleByCode: Partial<Record<KnownErrorCode, 'input' | 'signIn' | 'auth' | 'document' | 'connection' | 'operation'>> = {
  VALIDATION_ERROR: 'input', EMPTY_FILE: 'input', FILE_TOO_LARGE: 'input', UNSUPPORTED_MEDIA_TYPE: 'input', INVALID_ENCODING: 'input', MALFORMED_FDX: 'input', MALFORMED_PDF: 'input', INVALID_NATIVE_DOCUMENT: 'input', SOURCE_CLASSIFICATION_INCONCLUSIVE: 'input', ADAPTATION_VERIFICATION_FAILED: 'input',
  UNAUTHENTICATED: 'signIn', AUTHENTICATION_UNAVAILABLE: 'auth', ACCOUNT_BINDING_CONFLICT: 'auth',
  DOCUMENT_NOT_FOUND: 'document', DOCUMENT_CONTENT_MISSING: 'document', NOT_FOUND: 'document',
  SSE_CONNECTION_FAILED: 'connection', SSE_DISCONNECTED: 'connection',
}

const allCodes = new Set<string>([...API_ERROR_CODES, ...SSE_ERROR_CODES, ...FRONTEND_ERROR_CODES])

function translation(t: TFunction<'errors'>, key: string, values?: Record<string, unknown>): string {
  return (t as unknown as (translationKey: string, options?: Record<string, unknown>) => string)(key, values)
}

export function toUiError(error: unknown, fallbackCode: FrontendErrorCode): UiErrorDescriptor {
  if (error instanceof ApiClientError) return { code: error.code, context: error.context }
  if (typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string') {
    const candidate = error as { code: string; retryable?: unknown; context?: unknown }
    return {
      code: candidate.code,
      ...(typeof candidate.retryable === 'boolean' ? { retryable: candidate.retryable } : {}),
      ...(candidate.context && typeof candidate.context === 'object' && !Array.isArray(candidate.context) ? { context: candidate.context as Record<string, unknown> } : {}),
    }
  }
  return { code: fallbackCode }
}

export function localizeUiError(t: TFunction<'errors'>, error: UiErrorDescriptor): LocalizedUiError {
  if (!allCodes.has(error.code)) {
    return {
      title: translation(t, 'titles.operation'),
      message: translation(t, 'unknown.message', { code: error.code }),
    }
  }

  const code = error.code as KnownErrorCode
  const retryableKey = code === 'IMPORT_FAILED' && error.retryable ? 'IMPORT_FAILED_retryable' : code
  const localized: LocalizedUiError = {
    title: translation(t, `titles.${titleByCode[code] ?? 'operation'}`),
    message: translation(t, `codes.${retryableKey}`),
  }
  const correlationId = error.code === 'INTERNAL_ERROR' && typeof error.context?.correlation_id === 'string'
    ? error.context.correlation_id
    : undefined
  if (correlationId) localized.reference = translation(t, 'reference', { correlationId })
  return localized
}
