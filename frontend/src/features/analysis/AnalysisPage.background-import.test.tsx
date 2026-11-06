import { useVoiceLibrary, VoiceLibraryProvider } from '@/features/analysis/VoiceLibraryProvider'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { DocumentDetail } from '@/api/contracts'
import { ApiClientError } from '@/api/client'
import { discardImportDraft, getDocument, getVersions, importDocument, updateDocument } from '@/api/documents'
import { streamTaskProgress, type TaskStreamCallbacks } from '@/api/tasks'
import { createVoice, getVoice, listVoices, previewVoice, type VoiceSummary } from '@/api/voices'
import { getPlaybackSettings } from '@/api/playback'
import { TooltipProvider } from '@/components/ui/tooltip'
import { LocaleProvider, createLocaleRuntime } from '@/i18n/LocaleProvider'
import { AnalysisPage } from './AnalysisPage'
import { ImportSessionProvider } from './ImportSessionProvider'
vi.mock('@/api/documents', async (original) => ({ ...await original<typeof import('@/api/documents')>(), discardImportDraft: vi.fn(), getDocument: vi.fn(), getVersions: vi.fn(), importDocument: vi.fn(), updateDocument: vi.fn() }))
vi.mock('@/api/tasks', () => ({ cancelTask: vi.fn(), streamTaskProgress: vi.fn() }))
vi.mock('@/api/voices', async (original) => ({ ...await original<typeof import('@/api/voices')>(), createVoice: vi.fn(), getVoice: vi.fn(), listVoices: vi.fn(), previewVoice: vi.fn() }))
vi.mock('@/api/playback', async (original) => ({ ...await original<typeof import('@/api/playback')>(), getPlaybackSettings: vi.fn() }))
vi.mock('./DocumentList', () => ({ DocumentList: ({ onRequestNavigate }: { onRequestNavigate: (id: string) => void }) => <><button onClick={() => onRequestNavigate('A')}>Document A</button><button onClick={() => onRequestNavigate('B')}>Document B</button></> }))
vi.mock('./AnalysisTabsPanel', () => ({ AnalysisTabsPanel: () => null }))
vi.mock('@/components/NarravantHeader', () => ({ NarravantHeader: ({ actions }: { actions: ReactNode }) => { const { openManagement } = useVoiceLibrary(); return <TooltipProvider><button onClick={openManagement}>Manage Voices</button>{actions}</TooltipProvider> } }))
vi.mock('react-resizable-panels', () => ({ Group: ({ children }: { children: ReactNode }) => <div>{children}</div>, Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>, Separator: () => null }))
class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static instances: FakeWebSocket[] = []
  readyState = 1
  sentMessages: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  url: string
  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this); setTimeout(() => this.onopen?.(), 0) }
  send(message: string) { this.sentMessages.push(message) }
  close() { this.readyState = 3 }
}
function doc(id: string): DocumentDetail {
  return { document_id: id, title: id, owner_user_id: 'synthetic', owner_email: '', shared_count: 0,
    current_version_id: 1, version_id: 1, expected_version: 1, is_saved: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', capabilities: { can_edit: true, can_delete: true, can_share: false },
    source_fountain: `INT. ROOM - DAY #1#\n\n@Narrator\nSynthetic ${id}.`,
    scenes: [{ scene_number: 1, heading: 'INT. ROOM - DAY', text: '', dialogues: [] }], metadata: {},
    analysis: { status: 'completed', characters: [], turning_points: [] }, narrator: { voice_traits: 'Warm synthetic narrator' }, voice_assignments: [],
    emotion_arc: { valence: [4], tension: [0], characters: {}, scene_mapping: [], valence_vector: [] },
  }
}
function pending() {
  const value = doc('Imported')
  return { ...value, expected_version: null }
}
let callbacks: TaskStreamCallbacks
function ExternalRoute() { const navigate = useNavigate(); return <button onClick={() => navigate('/analysis/A')}>External route A</button> }
function setup(path = '/analysis/A') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}><VoiceLibraryProvider><MemoryRouter initialEntries={[path]}><ImportSessionProvider><ExternalRoute /><Routes><Route path="/analysis/new" element={<AnalysisPage />} /><Route path="/analysis/:documentId" element={<AnalysisPage />} /></Routes></ImportSessionProvider></MemoryRouter></VoiceLibraryProvider></LocaleProvider></QueryClientProvider>)
  return { ...view, client, user: userEvent.setup() }
}
beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('AudioContext', class {
    state = 'running'; currentTime = 0; sampleRate = 24000; destination = {}
    createBuffer(_channels: number, length: number, sampleRate: number) { return { length, sampleRate, getChannelData: () => new Float32Array(length) } }
    createBufferSource() { return { buffer: null, connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null } }
    close() { return Promise.resolve() }
  })
  vi.mocked(getDocument).mockImplementation(async (id) => doc(id))
  vi.mocked(getVersions).mockResolvedValue({ items: [] })
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'import-1', status: 'queued', task_type: 'document_import' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  vi.mocked(discardImportDraft).mockResolvedValue(undefined)
  vi.mocked(updateDocument).mockImplementation(async (id, data) => ({ ...doc(id), source_fountain: data.fountain_text ?? doc(id).source_fountain }))
  vi.mocked(createVoice).mockResolvedValue({ speaker: 'Narrator', voice_id: 'voice_synthetic', voice_traits: 'Warm synthetic narrator' })
})
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals() })
async function startAndHide(container: HTMLElement) {
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(['synthetic long input '.repeat(20000)], 'long.fountain')] } })
  await screen.findByRole('dialog', { name: '文書の取り込み' })
  fireEvent.click(screen.getByRole('button', { name: '閉じる' }))
}
it.each(['completed', 'failed', 'cancelled'] as const)('A→Bの編集・保存・Voice・PlayはImport %sで変化しない', async (terminal) => {
  const { container, client, user } = setup()
  await screen.findByRole('textbox', { name: '脚本本文' })
  await startAndHide(container)
  await user.click(screen.getByText('Document B'))
  const editor = await screen.findByRole('textbox', { name: '脚本本文' })
  await waitFor(() => expect(editor).toHaveValue(doc('B').source_fountain))
  const edited = doc('B').source_fountain + '\nLatest B.'
  fireEvent.change(editor, { target: { value: edited } })
  await user.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(updateDocument).toHaveBeenCalledWith('B', expect.objectContaining({ fountain_text: edited })))
  await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeDisabled())
  await user.click(screen.getByRole('button', { name: '話者の操作メニュー' }))
  await user.click(screen.getByRole('menuitem', { name: '全話者の声を生成' }))
  await waitFor(() => expect(createVoice).toHaveBeenCalledWith('B', expect.objectContaining({ speaker: 'Narrator' })))
  await user.click(within(await screen.findByRole('dialog', { name: '声の生成結果' })).getByRole('button', { name: '閉じる' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled())
  ;(editor as HTMLTextAreaElement).setSelectionRange(0, 0)
  fireEvent.select(editor)
  await user.click(screen.getByRole('button', { name: '再生開始' }))
  await waitFor(() => expect(FakeWebSocket.instances[0]?.sentMessages).toHaveLength(1))
  const ws = FakeWebSocket.instances[0]
  expect(JSON.parse(ws.sentMessages[0]).draft).toEqual({ source_fountain: edited, voice_assignments: [{ speaker: 'Narrator', voice_id: 'voice_synthetic', voice_traits: 'Warm synthetic narrator' }] })
  act(() => { ws.onmessage?.({ data: JSON.stringify({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 }) }); ws.onmessage?.({ data: JSON.stringify({ event: 'audio_chunk', data: 'AAA=', buffer_id: 1 }) }) })
  await waitFor(() => expect(editor).toHaveAttribute('readonly'))
  act(() => {
    for (let i = 0; i < 50; i++) callbacks.onProgress?.({ phase: 'analyzing', percentage: i, message: 'synthetic progress' })
    if (terminal === 'completed') callbacks.onCompleted?.({ import_draft: pending() })
    if (terminal === 'failed') callbacks.onError?.({ code: 'PROCESS_RESTARTED', message: 'PRIVATE_PROVIDER', retryable: false })
    if (terminal === 'cancelled') callbacks.onCancelled?.({ message: 'Cancelled' })
  })
  expect(editor).toHaveValue(edited)
  expect(ws.readyState).toBe(1)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(importDocument).toHaveBeenCalledTimes(1)
  if (terminal === 'completed') {
    await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
    await user.click(screen.getByRole('button', { name: '結果を開く' }))
    await user.click(screen.getByRole('button', { name: '編集に戻る' }))
    expect(ws.readyState).toBe(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
    await user.click(screen.getByRole('button', { name: '結果を開く' }))
    await user.click(screen.getByRole('button', { name: '破棄して結果を開く' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(pending().source_fountain))
    expect(ws.readyState).toBe(3)
  }
  client.clear()
})
it('前Importは次の開始・失敗で保持し、discard失敗でも両方残し404なら置換する', async () => {
  const { container, client, user } = setup('/analysis/new')
  await startAndHide(container)
  act(() => callbacks.onCompleted?.({ import_draft: pending() }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  await user.click(screen.getByRole('button', { name: '結果を開く' }))
  await startAndHide(container)
  act(() => callbacks.onError?.({ code: 'IMPORT_FAILED', message: 'synthetic', retryable: false }))
  expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(pending().source_fountain)
  expect(discardImportDraft).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 失敗' }))
  await user.click(screen.getByRole('button', { name: 'OK' }))
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'import-2', status: 'queued', task_type: 'document_import' })
  await startAndHide(container)
  act(() => callbacks.onCompleted?.({ import_draft: { ...pending(), source_fountain: 'INT. NEW - DAY #1#\n\nNew import.' } }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  await user.click(screen.getByRole('button', { name: '結果を開く' }))
  vi.mocked(discardImportDraft).mockRejectedValueOnce(new Error('offline'))
  await user.click(screen.getByRole('button', { name: '破棄して結果を開く' }))
  await user.click(await screen.findByRole('button', { name: 'Close' }))
  if (!screen.queryByRole('button', { name: '結果を開く' })) await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  await user.click(screen.getByRole('button', { name: '結果を開く' }))
  vi.mocked(discardImportDraft).mockRejectedValueOnce(new ApiClientError(404, 'NOT_FOUND', 'gone'))
  await user.click(screen.getByRole('button', { name: '破棄して結果を開く' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue('INT. NEW - DAY #1#\n\nNew import.'))
  expect(discardImportDraft).toHaveBeenCalledWith('import-1')
  client.clear()
})
it('SaveとVoice生成の応答待ち、編集dialog中は結果を適用しない', async () => {
  const { container, client, user } = setup('/analysis/B')
  const editor = await screen.findByRole('textbox', { name: '脚本本文' })
  await startAndHide(container)
  let finishSave!: (value: DocumentDetail) => void
  vi.mocked(updateDocument).mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve }))
  const edited = doc('B').source_fountain + '\nPending save.'
  fireEvent.change(editor, { target: { value: edited } })
  await user.click(screen.getByRole('button', { name: '保存' }))
  act(() => callbacks.onCompleted?.({ import_draft: pending() }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  expect(screen.getByRole('button', { name: '結果を開く' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  await act(async () => finishSave({ ...doc('B'), source_fountain: edited }))
  let finishVoice!: (value: Awaited<ReturnType<typeof createVoice>>) => void
  vi.mocked(createVoice).mockImplementationOnce(() => new Promise((resolve) => { finishVoice = resolve }))
  await user.click(screen.getByRole('button', { name: '話者の操作メニュー' }))
  await user.click(screen.getByRole('menuitem', { name: '全話者の声を生成' }))
  await waitFor(() => expect(createVoice).toHaveBeenCalledTimes(1))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  expect(screen.getByRole('button', { name: '結果を開く' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  await act(async () => finishVoice({ speaker: 'Narrator', voice_id: 'voice_synthetic', voice_traits: 'Warm synthetic narrator' }))
  await user.click(within(await screen.findByRole('dialog', { name: '声の生成結果' })).getByRole('button', { name: '閉じる' }))
  await user.click(screen.getByRole('button', { name: 'ナレーターを開く' }))
  await user.click(screen.getByRole('button', { name: 'ナレーターを編集' }))
  // dialogが開いている間の完了通知も、個別Voice draftをunmountしない。
  expect(screen.getByRole('textbox', { name: 'ナレーター 特徴' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'キャンセル' }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  expect(screen.getByRole('button', { name: '結果を開く' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  expect(editor).toHaveValue(edited)
  expect(discardImportDraft).not.toHaveBeenCalled()
  client.clear()
})
it('確認中にrouteが変われば適用せず、最新文書で確認し直す', async () => {
  const { container, client, user } = setup('/analysis/B')
  const editor = await screen.findByRole('textbox', { name: '脚本本文' })
  fireEvent.change(editor, { target: { value: doc('B').source_fountain + '\nUnsaved.' } })
  await startAndHide(container)
  act(() => callbacks.onCompleted?.({ import_draft: pending() }))
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 完了' }))
  await user.click(screen.getByRole('button', { name: '結果を開く' }))
  // browser側のroute変更は範囲外の離脱操作として再現し、結果適用の再検査を確認する。
  fireEvent.click(screen.getByText('External route A'))
  await waitFor(() => expect(getDocument).toHaveBeenCalledWith('A'))
  await user.click(screen.getByRole('button', { name: '破棄して結果を開く' }))
  expect(screen.getByRole('button', { name: '結果を開く' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(doc('A').source_fountain))
  expect(discardImportDraft).not.toHaveBeenCalled()
  client.clear()
})
it('文書一覧の移動もdirty guardへ委譲し、取消は編集を残す', async () => {
  const { client, user } = setup('/analysis/A')
  const editor = await screen.findByRole('textbox', { name: '脚本本文' })
  const edited = doc('A').source_fountain + '\nUnsaved A.'
  fireEvent.change(editor, { target: { value: edited } })
  await user.click(screen.getByText('Document B'))
  await user.click(screen.getByRole('button', { name: '編集に戻る' }))
  expect(editor).toHaveValue(edited)
  expect(getDocument).not.toHaveBeenCalledWith('B')
  await user.click(screen.getByText('Document B'))
  await user.click(screen.getByRole('button', { name: '破棄して移動' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(doc('B').source_fountain))
  client.clear()
})
it.each(['loading', 'error'] as const)('文書%s画面にも同じImportの操作を保持する', async (state) => {
  if (state === 'loading') vi.mocked(getDocument).mockImplementation(() => new Promise(() => {}))
  else vi.mocked(getDocument).mockRejectedValue(new Error('synthetic load failure'))
  const { container, client, user } = setup('/analysis/A')
  await startAndHide(container)
  expect(screen.getByRole('button', { name: '文書の取り込み: 処理中…' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '文書の取り込み: 処理中…' }))
  expect(await screen.findByRole('dialog')).toHaveAccessibleName('文書の取り込み')
  expect(importDocument).toHaveBeenCalledTimes(1)
  client.clear()
})


it('background Import completion preserves management, preview/Assign boundaries and next-Play settings', async () => {
  const voice: VoiceSummary = { voice_id: 'voice_candidate', display_name: 'Warm Velvet', availability: 'available', expiry_state: 'unknown', speaker_label: null, voice_traits: 'Warm synthetic narrator', created_at: null, created_at_source: 'unknown', expire_time: null, last_checked_at: null, usage_count: 0, language_code: 'en-US' } as VoiceSummary
  vi.mocked(listVoices).mockResolvedValue({ voices: [voice], complete: true, checked_at: null })
  vi.mocked(getVoice).mockResolvedValue(voice)
  vi.mocked(previewVoice).mockResolvedValue(new Uint8Array(2))
  vi.mocked(getPlaybackSettings).mockResolvedValue({ scene_pause_duration_ms: 1700, speaker_change_pause_duration_ms: 600, narrator_sentence_pause_duration_ms: 800, min_ms: 0, max_ms: 60000, step_ms: 100 })
  const { container, client, user } = setup('/analysis/B')
  const editor = await screen.findByRole('textbox', { name: '脚本本文' })
  await startAndHide(container)
  await user.click(screen.getByRole('button', { name: 'Manage Voices' }))
  expect(await screen.findByRole('listitem')).toHaveTextContent('Warm Velvet')
  await user.click(screen.getByRole('button', { name: '閉じる' }))
  await user.click(screen.getByRole('button', { name: 'ナレーターを開く' }))
  await user.click(screen.getByRole('button', { name: 'ナレーターを編集' }))
  const outer = screen.getByRole('dialog')
  fireEvent.change(within(outer).getByRole('textbox', { name: 'Narrator チェック用文章' }), { target: { value: 'Latest synthetic check.' } })
  await user.click(within(outer).getByRole('button', { name: '割り当てる' }))
  const library = await screen.findByRole('dialog', { name: 'Manage Voices' })
  await user.click(await within(library).findByRole('button', { name: 'Warm Velvet を試聴' }))
  expect(previewVoice).toHaveBeenCalledWith('B', { voice_id: voice.voice_id, text: 'Latest synthetic check.' }, fetch, false, expect.any(AbortSignal))
  await user.click(within(library).getByRole('button', { name: '割り当てる' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Manage Voices' })).not.toBeInTheDocument())
  await user.click(within(outer).getByRole('button', { name: 'キャンセル' }))
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'ナレーターを編集' }))
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Narrator 生成' }))
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'OK' }))
  await user.click(screen.getByRole('button', { name: '再生設定' }))
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'シーン間の間隔 (sec)' }), { target: { value: '1.2' } })
  await user.click(screen.getByRole('button', { name: 'OK' }))
  act(() => callbacks.onCompleted?.({ import_draft: pending() }))
  expect(editor).toHaveValue(doc('B').source_fountain)
  await user.click(screen.getByRole('button', { name: '再生開始' }))
  await waitFor(() => expect(FakeWebSocket.instances[0]?.sentMessages.length).toBeGreaterThan(0))
  const start = JSON.parse(FakeWebSocket.instances[0].sentMessages[0])
  expect(start.scene_pause_duration_ms).toBe(1200)
  expect(start.draft.voice_assignments).toEqual([{ speaker: 'Narrator', voice_id: 'voice_synthetic', voice_traits: 'Warm synthetic narrator' }])
  expect(start.draft.source_fountain).toContain('Synthetic B.')
  expect(createVoice).toHaveBeenCalledTimes(1)
  expect(updateDocument).not.toHaveBeenCalled()
  expect(discardImportDraft).not.toHaveBeenCalled()
  client.clear()
})
