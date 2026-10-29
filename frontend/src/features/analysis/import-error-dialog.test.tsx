import { render } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { describe, expect, it, vi } from 'vitest'

import { createNarravantI18n } from '@/i18n/i18n'
import { en } from '@/i18n/resources/en'
import { ja } from '@/i18n/resources/ja'
import { TaskProgressDialog } from './TaskProgressDialog'
import { importTaskRuntimeError } from './import-error'

function errorsT(locale: 'en' | 'ja') {
  return createNarravantI18n(locale).getFixedT(locale, 'errors') as TFunction<'errors'>
}

describe('Importの再試行案内', () => {
  it.each(['ja', 'en'] as const)('整合性検証の停止理由・場面・改稿回数を%sで表示する', (locale) => {
    const error = importTaskRuntimeError(errorsT(locale), {
      code: 'ADAPTATION_VERIFICATION_FAILED',
      message: 'PRIVATE_PROVIDER_DESCRIPTION',
      retryable: false,
      context: {
        reason: 'major_issues_after_revision', scene_number: 2,
        revision_count: 2, max_retries: 2, issue_types: ['event_omission'],
      },
    })
    expect(error.message).toContain(locale === 'ja' ? '場面2' : 'Scene 2')
    expect(error.message).toContain(locale === 'ja' ? '2/2回' : '2/2 retries')
    expect(error.message).toContain(locale === 'ja' ? '重要な出来事の欠落' : 'missing important events')
    expect(error.message).toContain(locale === 'ja' ? '重大な問題が残っています' : 'major issues remain')
    expect(error.message).not.toContain('PRIVATE_')
  })

  it('旧サーバーのcontextなしエラーでもコードに対応する理由を表示する', () => {
    expect(importTaskRuntimeError(errorsT('ja'), {
      code: 'ADAPTATION_VERIFICATION_FAILED', message: 'PRIVATE_MESSAGE', retryable: false,
    }).message).toBe(ja.errors.codes.ADAPTATION_VERIFICATION_FAILED)
  })

  it('未知の停止理由を本文として表示しない', () => {
    const error = importTaskRuntimeError(errorsT('ja'), {
      code: 'ADAPTATION_VERIFICATION_FAILED', message: 'PRIVATE_MESSAGE', retryable: false,
      context: { reason: 'PRIVATE_REASON', scene_number: 'PRIVATE_SCENE' },
    })
    expect(error.message).toBe(ja.errors.codes.ADAPTATION_VERIFICATION_FAILED)
    expect(error.message).not.toContain('PRIVATE_')
  })

  it('Geminiの再試行可能な失敗では進捗dialogを閉じ、現在のlocaleで再Importを案内する', () => {
    const error = importTaskRuntimeError(errorsT('ja'), {
      code: 'IMPORT_FAILED',
      message: 'backend message',
      retryable: true,
    })
    const { container } = render(
      <TaskProgressDialog
        now={Date.now()}
        onCancel={vi.fn()}
        onDismiss={vi.fn()}
        task={{
          operation: 'import',
          phase: 'classifying',
          percentage: 30,
          startedAt: Date.now(),
          terminal: 'error',
        }}
      />,
    )

    expect(container).toBeEmptyDOMElement()
    expect(error).toEqual({
      title: ja.errors.titles.operation,
      message: ja.errors.codes.IMPORT_FAILED_retryable,
    })
  })

  it('英語localeでは英語の案内を返す', () => {
    const error = importTaskRuntimeError(errorsT('en'), {
      code: 'IMPORT_FAILED',
      message: 'backend message',
      retryable: true,
    })
    expect(error).toEqual({
      title: en.errors.titles.operation,
      message: en.errors.codes.IMPORT_FAILED_retryable,
    })
  })

  it('再試行不能な失敗ではIMPORT_FAILEDの案内を返す', () => {
    const error = importTaskRuntimeError(errorsT('ja'), {
      code: 'IMPORT_FAILED',
      message: 'backend message',
      retryable: false,
    })
    expect(error).toEqual({
      title: ja.errors.titles.operation,
      message: ja.errors.codes.IMPORT_FAILED,
    })
  })
})
