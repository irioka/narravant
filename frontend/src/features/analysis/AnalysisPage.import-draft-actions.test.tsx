import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteDocument,
  discardImportDraft,
  getDocument,
  getVersions,
  importDocument,
  reanalyzeImportDraftEmotionArc,
  saveImportDraft,
  updateDocument,
} from '@/api/documents'
import { streamTaskProgress } from '@/api/tasks'
import type { DocumentDetail } from '@/api/contracts'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisPage } from './AnalysisPage'

vi.mock('@/api/documents', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/documents')>(),
  deleteDocument: vi.fn(), discardImportDraft: vi.fn(), getDocument: vi.fn(), getVersions: vi.fn(),
  importDocument: vi.fn(), reanalyzeImportDraftEmotionArc: vi.fn(), saveImportDraft: vi.fn(), updateDocument: vi.fn(),
}))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
vi.mock('./DocumentList', () => ({ DocumentList: () => null }))
vi.mock('./AnalysisTabsPanel', () => ({
  AnalysisTabsPanel: ({ onReanalyze }: { onReanalyze?: () => void }) => onReanalyze
    ? <button onClick={onReanalyze}>Open Emotional Arc reanalysis</button>
    : null,
}))
vi.mock('@/components/NarravantHeader', () => ({
  NarravantHeader: ({ actions }: { actions: ReactNode }) => <TooltipProvider><header>{actions}</header></TooltipProvider>,
}))
vi.mock('./AnalysisContent', () => ({ AnalysisContent: () => null }))
vi.mock('./TaskProgressDialog', () => ({
  TaskProgressDialog: ({ onDismiss, task }: { onDismiss: () => void; task?: { terminal?: string } }) => task?.terminal
    ? <button onClick={onDismiss}>Dismiss task progress</button>
    : null,
}))
vi.mock('./AudiobookPlayer', () => ({ AudiobookPlayer: () => null }))
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => null,
}))

const savedDocument: DocumentDetail = {
  document_id: 'existing-document', owner_user_id: 'synthetic-owner', title: 'Saved source',
  current_version_id: 1, version_id: 1, expected_version: 1, is_saved: true,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  owner_email: 'owner@example.test', shared_count: 0,
  capabilities: { can_delete: true, can_edit: true, can_share: false },
  source_fountain: 'Title: Saved source\n\nINT. SAVED ROOM - DAY #1#\n\nSaved body.',
  scenes: [{ scene_number: 1, heading: 'INT. SAVED ROOM - DAY', text: 'Saved body.', dialogues: [] }],
  metadata: { synopsis: 'Synthetic synopsis.' }, narrator: { voice_traits: '' }, voice_assignments: [],
  emotion_arc: {
    valence: [4], tension: [0], characters: { ALICE: [4] },
    scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
    valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
  },
  analysis: {
    status: 'completed', turning_points: [],
    characters: [{
      name: 'ALICE', external_goal: 'Synthetic goal', internal_need: 'Synthetic need',
      fear_or_cost: 'Synthetic risk', obstacle: 'Synthetic obstacle', choice: 'Synthetic choice',
      agency: 'Synthetic agency', goal_to_outcome: 'Synthetic outcome', related_turning_points: [], voice_traits: '',
    }],
  },
}

function importedDraft(documentId: string, title: string, sourceFountain: string) {
  return {
    document_id: documentId,
    version_id: 1,
    expected_version: null,
    title,
    source_fountain: sourceFountain,
    scenes: [{ scene_number: 1, heading: 'INT. IMPORTED ROOM - DAY', text: '', dialogues: [] }],
    metadata: { ...savedDocument.metadata },
    analysis: { ...savedDocument.analysis, characters: [...savedDocument.analysis.characters] },
    emotion_arc: { ...savedDocument.emotion_arc, characters: { ...savedDocument.emotion_arc.characters } },
    narrator: savedDocument.narrator,
    voice_assignments: [],
  }
}

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={client}>
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/analysis/new" element={<AnalysisPage />} />
            <Route path="/analysis/:documentId" element={<AnalysisPage />} />
          </Routes>
        </MemoryRouter>
      </LocaleProvider>
    </QueryClientProvider>,
  )
  return { ...rendered, client }
}

afterEach(() => { cleanup(); vi.clearAllMocks() })
beforeEach(() => {
  vi.mocked(getDocument).mockResolvedValue(savedDocument)
  vi.mocked(getVersions).mockResolvedValue({
    items: [{ version_id: 1, created_at: savedDocument.created_at, is_current: true }],
  })
  vi.mocked(discardImportDraft).mockResolvedValue(undefined)
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'import-task-1', task_type: 'document_import', status: 'queued' })
  vi.mocked(reanalyzeImportDraftEmotionArc).mockResolvedValue({ task_id: 'reanalyze-task', task_type: 'emotion_arc_reanalysis', status: 'queued' })
  vi.mocked(saveImportDraft).mockResolvedValue({ document_id: 'second-import', version_id: 1, expected_version: 1 })
})

describe('Import後、Save前の操作', () => {
  it('Native JSON draftではDeleteを無効にし、Saveを有効にする', async () => {
    const nativeDraft = importedDraft('native-unsaved', 'Native import', 'Title: Native import\n\nINT. IMPORTED ROOM - DAY #1#\n\nNative body.')
    vi.mocked(streamTaskProgress).mockImplementation(async (_taskId, callbacks) => {
      await callbacks.onCompleted?.({ import_draft: nativeDraft } as never)
    })
    const { container, client } = renderPage('/analysis/existing-document')

    await screen.findByRole('textbox', { name: '脚本本文' })
    fireEvent.change(container.querySelector('#analysis-document-import')!, {
      target: { files: [new File(['synthetic native'], 'synthetic.native.json', { type: 'application/json' })] },
    })

    await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(nativeDraft.source_fountain))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss task progress' }))
    expect(screen.getByRole('button', { name: '削除' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    expect(deleteDocument).not.toHaveBeenCalled()
    client.clear()
  })

  it('Import draftを再Importでき、表示本文で再分析した結果をそのdraftのSaveに含める', async () => {
    const firstDraft = importedDraft('first-import', 'First import', 'Title: First\n\nINT. IMPORTED ROOM - DAY #1#\n\nFirst body.')
    const secondDraft = importedDraft('second-import', 'Second import', 'Title: Second\n\nINT. IMPORTED ROOM - DAY #1#\n\nSecond body.')
    const editedSource = 'Title: Second\n\nINT. IMPORTED ROOM - DAY #1#\n\n@ALICE\nEdited visible body.'
    const updatedArc = {
      valence: [6], tension: [1], characters: { ALICE: [6] },
      scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
      valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    }
    vi.mocked(importDocument)
      .mockResolvedValueOnce({ task_id: 'import-task-1', task_type: 'document_import', status: 'queued' })
      .mockResolvedValueOnce({ task_id: 'import-task-2', task_type: 'document_import', status: 'queued' })
    vi.mocked(streamTaskProgress).mockImplementation(async (taskId, callbacks) => {
      if (taskId === 'import-task-1') await callbacks.onCompleted?.({ import_draft: firstDraft } as never)
      if (taskId === 'import-task-2') await callbacks.onCompleted?.({ import_draft: secondDraft } as never)
      if (taskId === 'reanalyze-task') await callbacks.onCompleted?.({
        document_id: 'second-import', version_id: 1, reanalysis: { emotion_arc: updatedArc },
      } as never)
    })
    const { container, client } = renderPage('/analysis/new')

    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(['first synthetic'], 'first.fountain')] },
    })
    await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(firstDraft.source_fountain))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss task progress' }))
    expect(screen.getByRole('button', { name: '文書を取り込む' })).toBeEnabled()

    fireEvent.change(container.querySelector('#analysis-document-import')!, {
      target: { files: [new File(['second synthetic'], 'second.fountain')] },
    })
    await waitFor(() => expect(screen.getByRole('textbox', { name: '脚本本文' })).toHaveValue(secondDraft.source_fountain))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss task progress' }))
    expect(discardImportDraft).toHaveBeenCalledWith('import-task-1')
    expect(importDocument).toHaveBeenCalledTimes(2)

    fireEvent.change(screen.getByRole('textbox', { name: '脚本本文' }), { target: { value: editedSource } })
    fireEvent.click(await screen.findByRole('button', { name: 'Open Emotional Arc reanalysis' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reanalyze' }))
    await waitFor(() => expect(reanalyzeImportDraftEmotionArc).toHaveBeenCalledWith('import-task-2', editedSource))
    expect(saveImportDraft).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss task progress' }))

    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    const titleInput = screen.getByRole('textbox', { name: 'ドキュメントのタイトル' })
    fireEvent.change(titleInput, { target: { value: 'Second import saved' } })
    fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)

    await waitFor(() => expect(saveImportDraft).toHaveBeenCalledTimes(1))
    expect(saveImportDraft).toHaveBeenCalledWith('import-task-2', expect.objectContaining({
      title: 'Second import saved',
      fountain_text: editedSource,
      emotion_arc: updatedArc,
    }))
    expect(updateDocument).not.toHaveBeenCalled()
    client.clear()
  })
})
