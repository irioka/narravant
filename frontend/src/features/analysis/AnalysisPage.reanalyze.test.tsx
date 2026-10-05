import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getDocument, getVersions, reanalyzeEmotionArc, updateDocument } from '@/api/documents'
import { streamTaskProgress } from '@/api/tasks'
import type { DocumentDetail } from '@/api/contracts'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisPage } from './AnalysisPage'

vi.mock('@/api/documents', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/documents')>(),
  getDocument: vi.fn(),
  getVersions: vi.fn(),
  reanalyzeEmotionArc: vi.fn(),
  updateDocument: vi.fn(),
}))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
vi.mock('./DocumentList', () => ({ DocumentList: () => null }))
vi.mock('./AnalysisTabsPanel', () => ({
  AnalysisTabsPanel: ({ onReanalyze }: { onReanalyze?: () => void }) => onReanalyze
    ? <button onClick={onReanalyze}>Open Emotional Arc reanalysis</button>
    : null,
}))
vi.mock('@/components/NarravantHeader', () => ({ NarravantHeader: () => null }))
vi.mock('./TaskProgressDialog', () => ({ TaskProgressDialog: () => null }))
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => null,
}))

const baseDocument: DocumentDetail = {
  document_id: 'synthetic-document',
  owner_user_id: 'synthetic-owner',
  title: 'Synthetic screenplay',
  current_version_id: 1,
  version_id: 1,
  expected_version: 1,
  is_saved: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  owner_email: 'owner@example.test',
  shared_count: 0,
  capabilities: { can_delete: true, can_edit: true, can_share: false },
  source_fountain: 'INT. TEST ROOM - DAY\n\n@ALICE\nOriginal synthetic line.',
  scenes: [{ scene_number: 1, heading: 'INT. TEST ROOM - DAY', text: '', dialogues: [] }],
  metadata: { synopsis: 'Synthetic synopsis.' },
  narrator: { voice_traits: '' },
  voice_assignments: [],
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

afterEach(() => { cleanup(); vi.clearAllMocks() })
beforeEach(() => {
  vi.mocked(getDocument).mockResolvedValue(baseDocument)
  vi.mocked(getVersions).mockResolvedValue({
    items: [{ version_id: 1, created_at: baseDocument.created_at, is_current: true }],
  })
  vi.mocked(reanalyzeEmotionArc).mockResolvedValue({ task_id: 'synthetic-task', task_type: 'emotion_arc_reanalysis', status: 'queued' })
})

describe('AnalysisPage の未保存本文 Reanalyze', () => {
  it('画面上の本文を解析し、結果と本文を一度の保存にまとめる', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const draftSource = 'INT. TEST ROOM - DAY\n\n@ALICE\nUpdated synthetic line.'
    const updatedArc = {
      valence: [6], tension: [1], characters: { ALICE: [6] },
      scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
      valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    }
    vi.mocked(streamTaskProgress).mockImplementation(async (_taskId, callbacks) => {
      callbacks.onCompleted?.({
        document_id: baseDocument.document_id,
        version_id: 1,
        reanalysis: { emotion_arc: updatedArc },
      })
    })
    vi.mocked(updateDocument).mockResolvedValue({
      ...baseDocument, source_fountain: draftSource, emotion_arc: updatedArc, version_id: 2, current_version_id: 2, expected_version: 2,
    })

    render(
      <QueryClientProvider client={client}>
        <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
          <MemoryRouter initialEntries={['/analysis/synthetic-document']}>
            <Routes><Route path="/analysis/:documentId" element={<AnalysisPage />} /></Routes>
          </MemoryRouter>
        </LocaleProvider>
      </QueryClientProvider>,
    )

    const editor = await screen.findByRole('textbox', { name: '脚本本文' })
    fireEvent.change(editor, { target: { value: draftSource } })
    fireEvent.click(await screen.findByRole('button', { name: 'Open Emotional Arc reanalysis' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reanalyze' }))

    await waitFor(() => expect(reanalyzeEmotionArc).toHaveBeenCalledWith(baseDocument.document_id, draftSource))
    expect(updateDocument).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(updateDocument).toHaveBeenCalledTimes(1))
    expect(updateDocument).toHaveBeenCalledWith(baseDocument.document_id, expect.objectContaining({
      expected_version: 1,
      base_version_id: 1,
      fountain_text: draftSource,
      emotion_arc: updatedArc,
    }))

    client.clear()
  })
})
