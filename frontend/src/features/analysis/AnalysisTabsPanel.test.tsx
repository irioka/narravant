import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentDetail } from '@/api/contracts'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisTabsPanel } from './AnalysisTabsPanel'

vi.mock('./EmotionArcChart', () => ({
  EmotionArcChart: () => <div data-testid="emotion-arc-chart" />,
}))

const mockDocument: DocumentDetail = {
  document_id: 'doc-test-1',
  owner_user_id: 'user-1',
  title: 'Test Script',
  current_version_id: 1,
  version_id: 1,
  expected_version: 1,
  is_saved: true,
  created_at: '2026-09-14T00:00:00Z',
  updated_at: '2026-09-14T00:00:00Z',
  owner_email: 'test@example.com',
  shared_count: 0,
  capabilities: { can_delete: false, can_edit: true, can_share: false },
  source_fountain: 'INT. ROOM - DAY\nAlice walks in.',
  scenes: [{ scene_number: 1, heading: 'INT. ROOM - DAY', text: 'Alice walks in.', dialogues: [] }],
  metadata: { synopsis: 'A test synopsis.' },
  narrator: { voice_traits: '' },
  voice_assignments: [],
  emotion_arc: {
    valence: [4],
    tension: [0],
    characters: {},
    scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
    valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
  },
  analysis: {
    status: 'completed',
    characters: [],
    turning_points: [
      {
        tp_number: 1,
        label: 'Opportunity',
        availability: 'identified',
        scene_number: 1,
        change: 'Alice starts the journey.',
        involved_characters: [{ name: 'Alice', goal: 'Depart', conflict: 'Fear', choice: 'Take step', action: 'Leaves home', change: 'Determined' }],
      },
      {
        tp_number: 2,
        label: 'Change of Plans',
        availability: 'not_applicable',
        reason: '原作の構成上、計画の変更に該当する契機が存在しない。',
        involved_characters: [],
      },
    ],
  },
}

afterEach(() => cleanup())

function renderPanel() {
  return render(
    <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AnalysisTabsPanel document={mockDocument} onDraftChange={() => undefined} onSelectVersion={() => undefined} titleMode="editable" versions={[{ version_id: 1, created_at: '2026-09-14T00:00:00Z' }]} />
    </LocaleProvider>,
  )
}

describe('AnalysisTabsPanel', () => {
  it('既定で履歴タブを表示し、保存済みバージョンを並べる', () => {
    renderPanel()
    expect(screen.getByRole('tab', { name: '履歴' })).toBeInTheDocument()
    expect(screen.getByText(/^v1/)).toBeInTheDocument()
  })

  it('あらすじタブにあらすじ本文を表示する', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('tab', { name: 'あらすじ' }))
    const synopsisGroup = screen.getByRole('region', { name: 'あらすじ' })
    expect(synopsisGroup).toContainElement(screen.getByText('A test synopsis.'))
  })

  it('感情アークタブにチャートを表示する', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('tab', { name: '感情アーク' }))
    expect(screen.getByTestId('emotion-arc-chart')).toBeInTheDocument()
  })

  it('転換点タブで identified と not_applicable のカードを表示する', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('tab', { name: '転換点' }))
    expect(screen.getByLabelText('TP1を開く')).toBeInTheDocument()
    expect(screen.getByText('該当なし')).toBeInTheDocument()
    expect(screen.getByText('原作の構成上、計画の変更に該当する契機が存在しない。')).toBeInTheDocument()
    expect(screen.queryByLabelText('TP2を開く')).not.toBeInTheDocument()
  })
})
