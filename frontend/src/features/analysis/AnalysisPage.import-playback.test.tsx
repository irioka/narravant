import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'

import { getDocument, getVersions, importDocument, saveImportDraft, updateDocument } from '@/api/documents'
import { streamTaskProgress } from '@/api/tasks'
import { createVoice } from '@/api/voices'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisPage } from './AnalysisPage'

vi.mock('@/api/documents', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/documents')>(),
  importDocument: vi.fn(), getDocument: vi.fn(), getVersions: vi.fn(),
  saveImportDraft: vi.fn(), updateDocument: vi.fn(),
}))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
vi.mock('@/api/voices', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/voices')>(), createVoice: vi.fn(),
}))
vi.mock('./DocumentList', () => ({ DocumentList: () => null }))
vi.mock('./AnalysisTabsPanel', () => ({ AnalysisTabsPanel: () => null }))
vi.mock('@/components/NarravantHeader', () => ({ NarravantHeader: () => null }))
vi.mock('./TaskProgressDialog', () => ({ TaskProgressDialog: () => null }))
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>, Separator: () => null,
}))

class DraftWebSocket {
  static instances: DraftWebSocket[] = []
  readyState = 1
  sentMessages: string[] = []
  onopen: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    DraftWebSocket.instances.push(this)
    setTimeout(() => this.onopen?.(), 0)
  }
  send(message: string) { this.sentMessages.push(message) }
  close() { this.readyState = 3 }
}

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

it('Import直後にシーン移動し、生成した声で下書きを保存せず再生する', async () => {
  DraftWebSocket.instances = []
  vi.stubGlobal('WebSocket', DraftWebSocket)
  const draft = {
    document_id: 'unsaved-import', version_id: 1, expected_version: null, title: '合成サンプル',
    source_fountain: 'INT. ROOM - DAY #1#\n\n@ナレーター\n最初の場面。\n\nINT. GARDEN - DAY #2#\n\n@ナレーター\n次の場面。',
    scenes: [
      { scene_number: 1, heading: 'INT. ROOM - DAY', text: '', dialogues: [] },
      { scene_number: 2, heading: 'INT. GARDEN - DAY', text: '', dialogues: [] },
    ],
    metadata: {}, analysis: { status: 'completed' as const, characters: [], turning_points: [] },
    narrator: { voice_traits: '落ち着いた声' }, voice_assignments: [],
    emotion_arc: { valence: [], tension: [], characters: {}, scene_mapping: [], valence_vector: [] },
  }
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'import-task', status: 'queued', task_type: 'import_document' })
  vi.mocked(streamTaskProgress).mockImplementation(async (_id, callbacks) => {
    callbacks.onCompleted?.({ import_draft: draft })
  })
  const assignment = { speaker: 'ナレーター', voice_id: 'voices/synthetic', voice_traits: '落ち着いた声' }
  vi.mocked(createVoice).mockResolvedValue(assignment)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { container } = render(
    <QueryClientProvider client={client}>
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <MemoryRouter initialEntries={['/analysis/new']}>
          <Routes><Route path="/analysis/new" element={<AnalysisPage />} /></Routes>
        </MemoryRouter>
      </LocaleProvider>
    </QueryClientProvider>,
  )
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File([draft.source_fountain], 'synthetic.fountain')] },
  })
  const sceneInput = await screen.findByRole('spinbutton', { name: '再生開始シーン番号' })
  expect(sceneInput).toBeEnabled()
  expect(screen.getByRole('button', { name: '再生開始' })).toBeEnabled()
  fireEvent.change(sceneInput, { target: { value: '2' } })
  const editor = screen.getByRole('textbox', { name: '脚本本文' }) as HTMLTextAreaElement
  expect(editor.selectionStart).toBe(draft.source_fountain.indexOf('INT. GARDEN'))

  fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
  expect(await screen.findByText('声が作成されていません')).toBeInTheDocument()
  expect(DraftWebSocket.instances).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: '閉じる' }))
  fireEvent.click(screen.getByRole('button', { name: '全話者の声を生成' }))
  await waitFor(() => expect(createVoice).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByRole('button', { name: '全話者の声を生成' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
  await waitFor(() => expect(DraftWebSocket.instances[0]?.sentMessages).toHaveLength(1))
  const ws = DraftWebSocket.instances[0]
  expect(ws.url).toMatch(/\/api\/v1\/documents\/playback$/)
  expect(JSON.parse(ws.sentMessages[0])).toEqual({
    action: 'start', scene_number: 2, start_utterance_index: 0,
    draft: { source_fountain: draft.source_fountain, voice_assignments: [assignment] },
  })
  expect(getDocument).not.toHaveBeenCalled()
  expect(getVersions).not.toHaveBeenCalled()
  expect(saveImportDraft).not.toHaveBeenCalled()
  expect(updateDocument).not.toHaveBeenCalled()
  client.clear()
})
