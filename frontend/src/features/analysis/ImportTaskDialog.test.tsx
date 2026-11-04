import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { importDocument } from '@/api/documents'
import { cancelTask, streamTaskProgress, type TaskStreamCallbacks } from '@/api/tasks'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { ImportSessionProvider } from './ImportSessionProvider'
import { ImportAction } from './ImportAction'
import { ImportTaskDialog } from './ImportTaskDialog'
vi.mock('@/api/documents', () => ({ importDocument: vi.fn(), discardImportDraft: vi.fn() }))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
function setup() {
  const user = userEvent.setup()
  const runtime = createLocaleRuntime({ initialLocale: 'ja' })
  const view = render(<LocaleProvider runtime={runtime}><TooltipProvider><ImportSessionProvider><ImportAction /><ImportTaskDialog onOpenResult={vi.fn()} /></ImportSessionProvider></TooltipProvider></LocaleProvider>)
  return { user, runtime, ...view }
}
it('開始→閉じる→アイコン→確認取消→承認、Close/Escは通信しない', async () => {
  let callbacks!: TaskStreamCallbacks
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'same-task', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  vi.mocked(cancelTask).mockResolvedValue({ task_id: 'same-task', status: 'cancel_requested' })
  const { user, container } = setup()
  await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  await screen.findByRole('dialog', { name: '文書の取り込み' })
  expect(screen.queryByText('Import処理中…')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 処理中…' }))
  await user.click(screen.getByRole('button', { name: '取り込みダイアログを閉じる' }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 処理中…' }))
  await user.keyboard('{Escape}')
  expect(cancelTask).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 処理中…' }))
  await user.click(screen.getByRole('button', { name: 'キャンセル' }))
  await user.click(screen.getByRole('button', { name: '処理を続ける' }))
  expect(cancelTask).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'キャンセル' }))
  await user.click(screen.getByRole('button', { name: '中止する' }))
  await waitFor(() => expect(cancelTask).toHaveBeenCalledExactlyOnceWith('same-task'))
  expect(screen.getByRole('button', { name: 'キャンセル要求中…' })).toBeDisabled()
  expect(screen.getByText('キャンセル要求中… — 0%')).toBeInTheDocument()
  act(() => callbacks.onCancelled?.({ message: 'Cancelled' }))
  expect(screen.getByText('キャンセル済み — 0%')).toBeInTheDocument()
  expect(importDocument).toHaveBeenCalledTimes(1)
})
it('開始応答待ちの確認承認後、正しいIDへ1回だけ中止する', async () => {
  let accept!: (value: Awaited<ReturnType<typeof importDocument>>) => void
  vi.mocked(importDocument).mockImplementation(() => new Promise((resolve) => { accept = resolve }))
  vi.mocked(streamTaskProgress).mockImplementation(() => new Promise(() => {}))
  const { user, container } = setup()
  await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  await user.click(screen.getByRole('button', { name: 'キャンセル' }))
  await user.click(screen.getByRole('button', { name: '中止する' }))
  expect(cancelTask).not.toHaveBeenCalled()
  await act(async () => accept({ task_id: 'deferred-task', task_type: 'document_import', status: 'queued' }))
  expect(cancelTask).toHaveBeenCalledExactlyOnceWith('deferred-task')
})
it.each(['completed', 'cancelled', 'failed'] as const)('閉じている間の%sでも全処理時間を固定し、再表示で増えない', async (terminal) => {
  let callbacks!: TaskStreamCallbacks
  const clock = vi.spyOn(Date, 'now').mockReturnValue(100_000)
  try {
    vi.mocked(importDocument).mockResolvedValue({ task_id: 'timed-task', task_type: 'document_import', status: 'queued' })
    vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
    const { user, container } = setup()
    await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
    expect(await screen.findByRole('dialog', { name: '文書の取り込み' })).toBeInTheDocument()
    clock.mockReturnValue(130_000)
    act(() => callbacks.onProgress?.({ phase: 'analyzing', percentage: 90, message: 'Synthetic', received_characters: 3 }))
    await user.click(screen.getByRole('button', { name: '閉じる' }))
    clock.mockReturnValue(165_000)
    act(() => {
      if (terminal === 'completed') callbacks.onCompleted?.({ import_draft: { document_id: 'synthetic' } } as never)
      if (terminal === 'cancelled') callbacks.onCancelled?.({ message: 'Cancelled' })
      if (terminal === 'failed') callbacks.onError?.({ code: 'IMPORT_FAILED', message: 'Synthetic failure', retryable: false })
    })
    clock.mockReturnValue(200_000)
    const label = { completed: '完了', cancelled: 'キャンセル済み', failed: '失敗' }[terminal]
    await user.click(screen.getByRole('button', { name: `文書の取り込み: ${label}` }))
    expect(screen.getByText('経過: 65秒')).toBeInTheDocument()
    if (terminal === 'completed') {
      expect(screen.getByRole('progressbar', { name: '取り込みの進捗' })).toHaveAttribute('aria-valuenow', '100')
      expect(screen.getByText('完了 — 100%')).toBeInTheDocument()
      expect(screen.queryByText('Import完了', { exact: true })).not.toBeInTheDocument()
    }
    expect(screen.queryByText(/Analyzing structure/)).not.toBeInTheDocument()
    expect(screen.queryByText('3', { selector: 'output' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '閉じる' }))
    clock.mockReturnValue(300_000)
    await user.click(screen.getByRole('button', { name: `文書の取り込み: ${label}` }))
    expect(screen.getByText('経過: 65秒')).toBeInTheDocument()
  } finally { clock.mockRestore() }
})
it('開始・通信状態不明・再接続・完了でも同じ進捗欄へ状態を表示する', async () => {
  let accept!: (value: Awaited<ReturnType<typeof importDocument>>) => void
  let disconnect!: (error: Error) => void
  let callbacks!: TaskStreamCallbacks
  vi.mocked(importDocument).mockImplementation(() => new Promise((resolve) => { accept = resolve }))
  vi.mocked(streamTaskProgress)
    .mockImplementationOnce((_id, cb) => { callbacks = cb; return new Promise((_resolve, reject) => { disconnect = reject }) })
    .mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  const { user, container } = setup()
  await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  const bar = screen.getByRole('progressbar', { name: '取り込みの進捗' })
  expect(screen.getByText('開始中… — 0%')).toBeInTheDocument()
  await act(async () => accept({ task_id: 'same-task', task_type: 'document_import', status: 'queued' }))
  expect(screen.getByText('待機中 — 0%')).toBeInTheDocument()
  act(() => callbacks.onProgress?.({ phase: 'analyzing', percentage: 90, message: 'Synthetic', received_characters: 3 }))
  await act(async () => disconnect(new Error('synthetic offline')))
  expect(screen.getByText('状態不明 — 90%')).toBeInTheDocument()
  expect(screen.getByRole('progressbar', { name: '取り込みの進捗' })).toBe(bar)
  await user.click(screen.getByRole('button', { name: '進捗を再接続' }))
  expect(screen.getByText('構造と感情アークを分析中… — 90%')).toBeInTheDocument()
  act(() => callbacks.onCompleted?.({ import_draft: { document_id: 'synthetic' } } as never))
  expect(screen.getByText('完了 — 100%')).toBeInTheDocument()
  expect(screen.getByRole('progressbar', { name: '取り込みの進捗' })).toBe(bar)
  expect(importDocument).toHaveBeenCalledTimes(1)
})
it('日本語UIのタイトル・工程名を翻訳し、実行中の言語切替でも同じ処理を表示する', async () => {
  let callbacks!: TaskStreamCallbacks
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'localized-task', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  const { user, container, runtime } = setup()
  await user.upload(container.querySelector('input')!, new File(['synthetic'], 'synthetic.txt'))
  expect(screen.getByRole('dialog', { name: '文書の取り込み' })).toBeInTheDocument()
  act(() => callbacks.onProgress?.({ phase: 'analyzing', percentage: 90, message: 'Synthetic' }))
  expect(screen.getByText('構造と感情アークを分析中… — 90%')).toBeInTheDocument()
  await act(async () => { await runtime.i18n.changeLanguage('en') })
  expect(screen.getByRole('dialog', { name: 'Import document' })).toBeInTheDocument()
  expect(screen.getByText('Analyzing structure and emotional arc… — 90%')).toBeInTheDocument()
  await act(async () => { await runtime.i18n.changeLanguage('ja') })
  act(() => callbacks.onProgress?.({ phase: 'building_plot_graph', percentage: 20, message: 'Synthetic' }))
  expect(screen.getByText('因果プロットグラフを構築中… — 20%')).toBeInTheDocument()
  expect(importDocument).toHaveBeenCalledTimes(1)
})
