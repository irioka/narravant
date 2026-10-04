import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentDetail } from '@/api/contracts'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisTabsPanel } from './AnalysisTabsPanel'
import { AnalysisContent } from './AnalysisContent'

vi.mock('./EmotionArcChart', () => ({
  EmotionArcChart: () => <div data-testid="emotion-arc-chart" />,
}))

const ENGLISH_SYNOPSIS = 'The river rose, and she knew that her friend must find shelter.'
const ENGLISH_GOAL = 'Save the town from the flood.'
const ENGLISH_TP_CHANGE = 'Alice discovers the secret dam.'
const ENGLISH_CHAR_CHANGE = 'Alice becomes determined to survive.'
const ENGLISH_DIALOGUE = 'I will find a way through the storm.'

const mockDocument: DocumentDetail = {
  document_id: 'doc-test-lang',
  owner_user_id: 'user-1',
  title: 'Storm Cabin',
  current_version_id: 1,
  version_id: 1,
  expected_version: 1,
  is_saved: true,
  created_at: '2026-09-14T00:00:00Z',
  updated_at: '2026-09-14T00:00:00Z',
  owner_email: 'test@example.com',
  shared_count: 0,
  capabilities: { can_delete: false, can_edit: true, can_share: false },
  source_fountain: `INT. CABIN - DAY #1#\n\n@Alice\n${ENGLISH_DIALOGUE}\n`,
  scenes: [
    {
      scene_number: 1,
      heading: 'INT. CABIN - DAY',
      text: `@Alice\n${ENGLISH_DIALOGUE}`,
      dialogues: [{ character: 'Alice', line: ENGLISH_DIALOGUE }],
    },
  ],
  metadata: {
    title: 'Storm Cabin',
    synopsis: ENGLISH_SYNOPSIS,
  },
  narrator: { voice_traits: 'Calm and steady narrator tone.' },
  voice_assignments: [],
  emotion_arc: {
    valence: [4],
    tension: [0],
    characters: { Alice: [4] },
    scene_mapping: [{ point_number: 1, start_scene_number: 1, end_scene_number: 1, representative_scene_number: 1 }],
    valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
  },
  analysis: {
    status: 'completed',
    characters: [
      {
        name: 'Alice',
        external_goal: ENGLISH_GOAL,
        internal_need: 'Find peace.',
        fear_or_cost: 'Losing everything.',
        obstacle: 'Rising water.',
        choice: 'Build shelter.',
        agency: 'High',
        goal_to_outcome: 'Success',
        related_turning_points: [1],
        voice_traits: 'Clear, determined voice.',
      },
    ],
    turning_points: [
      {
        tp_number: 1,
        label: 'Opportunity',
        availability: 'identified',
        scene_number: 1,
        change: ENGLISH_TP_CHANGE,
        involved_characters: [
          {
            name: 'Alice',
            goal: ENGLISH_GOAL,
            conflict: 'Rising water',
            choice: 'Take shelter',
            action: 'Runs to cabin',
            change: ENGLISH_CHAR_CHANGE,
          },
        ],
      },
    ],
  },
}

afterEach(() => cleanup())

describe('Analysis UI language switching preserves content text verbatim', () => {
  it('AnalysisTabsPanel は ja/en 切替でラベルのみ変わり本文は不変', async () => {
    const user = userEvent.setup()

    // 1. Japanese locale
    const { unmount } = render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <AnalysisTabsPanel
          document={mockDocument}
          onDraftChange={() => undefined}
          onSelectVersion={() => undefined}
          titleMode="editable"
          versions={[{ version_id: 1, created_at: '2026-09-14T00:00:00Z' }]}
        />
      </LocaleProvider>,
    )

    // Check Japanese tab labels
    expect(screen.getByRole('tab', { name: '履歴' })).toBeInTheDocument()
    const synopsisTabJa = screen.getByRole('tab', { name: 'あらすじ' })
    expect(synopsisTabJa).toBeInTheDocument()
    await user.click(synopsisTabJa)

    // Body content must remain original English
    expect(screen.getByText(ENGLISH_SYNOPSIS)).toBeInTheDocument()

    // Switch to Turning points tab
    const tpTabJa = screen.getByRole('tab', { name: '転換点' })
    await user.click(tpTabJa)
    const tpTriggerJa = screen.getByLabelText('TP1を開く')
    expect(tpTriggerJa).toBeInTheDocument()
    await user.click(tpTriggerJa)
    expect(screen.getByText(ENGLISH_TP_CHANGE)).toBeInTheDocument()
    expect(screen.getByText(ENGLISH_CHAR_CHANGE)).toBeInTheDocument()

    unmount()

    // 2. English locale
    render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}>
        <AnalysisTabsPanel
          document={mockDocument}
          onDraftChange={() => undefined}
          onSelectVersion={() => undefined}
          titleMode="editable"
          versions={[{ version_id: 1, created_at: '2026-09-14T00:00:00Z' }]}
        />
      </LocaleProvider>,
    )

    // Check English tab labels
    expect(screen.getByRole('tab', { name: 'History' })).toBeInTheDocument()
    const synopsisTabEn = screen.getByRole('tab', { name: 'Synopsis' })
    expect(synopsisTabEn).toBeInTheDocument()
    await user.click(synopsisTabEn)

    // Body content must still be exact original English
    expect(screen.getByText(ENGLISH_SYNOPSIS)).toBeInTheDocument()

    // Switch to Turning Points tab
    const tpTabEn = screen.getByRole('tab', { name: 'Turning Points' })
    await user.click(tpTabEn)
    const tpTriggerEn = screen.getByLabelText('Open TP1')
    expect(tpTriggerEn).toBeInTheDocument()
    await user.click(tpTriggerEn)
    expect(screen.getByText(ENGLISH_TP_CHANGE)).toBeInTheDocument()
    expect(screen.getByText(ENGLISH_CHAR_CHANGE)).toBeInTheDocument()
  })

  it('AnalysisContent は ja/en 切替でキャラクター本文を変更しない', async () => {
    const user = userEvent.setup()

    // 1. Japanese locale
    const { unmount } = render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <AnalysisContent
          document={mockDocument}
          titleMode="editable"
          onDraftChange={() => undefined}
        />
      </LocaleProvider>,
    )

    expect(screen.getByRole('heading', { name: '登場人物' })).toBeInTheDocument()
    const triggerJa = screen.getByLabelText('Aliceを開く')
    expect(triggerJa).toBeInTheDocument()
    await user.click(triggerJa)
    expect(screen.getByText(ENGLISH_GOAL)).toBeInTheDocument()

    unmount()

    // 2. English locale
    render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}>
        <AnalysisContent
          document={mockDocument}
          titleMode="editable"
          onDraftChange={() => undefined}
        />
      </LocaleProvider>,
    )

    expect(screen.getByRole('heading', { name: 'Characters' })).toBeInTheDocument()
    const triggerEn = screen.getByLabelText('Expand Alice')
    expect(triggerEn).toBeInTheDocument()
    await user.click(triggerEn)
    expect(screen.getByText(ENGLISH_GOAL)).toBeInTheDocument()
  })
})