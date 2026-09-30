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
