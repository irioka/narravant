import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { importDocument } from '@/api/documents'
import { streamTaskProgress, type TaskStreamCallbacks } from '@/api/tasks'
import { TooltipProvider } from '@/components/ui/tooltip'
import { LocaleProvider, createLocaleRuntime } from '@/i18n/LocaleProvider'
import { ImportSessionProvider } from './ImportSessionProvider'
import { ImportAction } from './ImportAction'
vi.mock('@/api/documents', () => ({ importDocument: vi.fn(), discardImportDraft: vi.fn() }))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
it.each(['ja', 'en'] as const)('開始結果不明でも枠と日英ラベルを保持する %s', async (locale) => {
  vi.mocked(importDocument).mockRejectedValue(new TypeError('synthetic offline'))
  const { container } = render(<LocaleProvider runtime={createLocaleRuntime({ initialLocale: locale })}><TooltipProvider><ImportSessionProvider><ImportAction /></ImportSessionProvider></TooltipProvider></LocaleProvider>)
  await userEvent.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  expect(await screen.findByRole('button', { name: locale === 'ja' ? '文書の取り込み: 状態不明' : 'Import document: Status unknown' })).toBeEnabled()
})
it('マウスオーバー中のTipsもja・en切替と処理状態に追従する', async () => {
  let callbacks!: TaskStreamCallbacks
  const user = userEvent.setup()
  const runtime = createLocaleRuntime({ initialLocale: 'ja' })
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'localized-task', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  const { container } = render(<LocaleProvider runtime={runtime}><TooltipProvider><ImportSessionProvider><ImportAction /></ImportSessionProvider></TooltipProvider></LocaleProvider>)
  await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  await user.hover(await screen.findByRole('button', { name: '文書の取り込み: 処理中…' }))
  expect(await screen.findByRole('tooltip')).toHaveTextContent('文書の取り込み: 処理中…')
  await act(async () => { await runtime.i18n.changeLanguage('en') })
  expect(screen.getByRole('tooltip')).toHaveTextContent('Import document: Running…')
  act(() => callbacks.onCompleted?.({ import_draft: { document_id: 'synthetic' } } as never))
  expect(screen.getByRole('tooltip')).toHaveTextContent('Import document: Completed')
  await act(async () => { await runtime.i18n.changeLanguage('ja') })
  expect(screen.getByRole('tooltip')).toHaveTextContent('文書の取り込み: 完了')
  expect(importDocument).toHaveBeenCalledTimes(1)
})
