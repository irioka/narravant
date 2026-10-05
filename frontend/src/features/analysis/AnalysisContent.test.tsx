import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError } from '@/api/client'
import type { DocumentDetail } from '@/api/contracts'
import { VOICE_LIMIT_EXCEEDED } from '@/api/voices'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AnalysisContent, type AnalysisDraftUpdate } from './AnalysisContent'

// Mock the voices API so tests never call the real (billable) endpoints.
vi.mock('@/api/voices', async () => {
  const actual = await vi.importActual<typeof import('@/api/voices')>('@/api/voices')
  return {
    ...actual,
    createVoice: vi.fn(),
    previewVoice: vi.fn(),
  }
})

// Mock the PCM playback helper so no AudioContext is needed.
vi.mock('./audio-decoder', async () => {
  const actual = await vi.importActual<typeof import('./audio-decoder')>('./audio-decoder')
  return { ...actual, playPcm16: vi.fn().mockResolvedValue(undefined) }
})

import { createVoice, previewVoice } from '@/api/voices'
import { playPcm16 } from './audio-decoder'

const FOUNTAIN = [
  'INT. ROOM - DAY',
  '',
  '@Alice',
  '（決意を込めて）',
  '',
  'I will save this town.',
].join('\n')

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
  capabilities: {
    can_delete: false,
    can_edit: true,
    can_share: false,
  },
  source_fountain: FOUNTAIN,
  scenes: [
    {
      scene_number: 1,
      heading: 'INT. ROOM - DAY',
      text: 'Alice walks in.',
      dialogues: [],
    },
  ],
  metadata: {
    synopsis: 'A test synopsis.',
    theme_setting: 'A test theme.',
  },
  narrator: { voice_traits: '知的な語り手' },
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
    characters: [
      {
        name: 'Alice',
        voice_traits: '勇敢な少女声',
        external_goal: 'Save town',
        internal_need: 'Courage',
        fear_or_cost: 'Death',
        obstacle: 'Monster',
        choice: 'Fight',
        agency: 'High',
        goal_to_outcome: 'Town saved',
        related_turning_points: [1],
      },
    ],
    turning_points: [],
  },
}

function documentWithVoice(): DocumentDetail {
  return {
    ...mockDocument,
    voice_assignments: [{ speaker: 'Alice', voice_id: 'voice-abc', voice_traits: '勇敢な少女声' }],
  }
}

afterEach(() => cleanup())
beforeEach(() => vi.clearAllMocks())

function renderContent(document: DocumentDetail = mockDocument, onDraftChange?: (update: AnalysisDraftUpdate) => void) {
  return render(
    <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AnalysisContent document={document} onDraftChange={onDraftChange ?? (() => undefined)} titleMode="editable" />
    </LocaleProvider>,
  )
}

describe('AnalysisContent 登場人物セクション', () => {
  it('テーマや文書タイトルヘッダーを表示せず、登場人物見出しを出す', () => {
    renderContent()
    expect(screen.queryByText('テーマ')).not.toBeInTheDocument()
    expect(screen.queryByText('A test theme.')).not.toBeInTheDocument()
    expect(screen.getByText('登場人物')).toBeInTheDocument()
    expect(screen.queryByText(/追加したキャラクターの Emotional arc/)).not.toBeInTheDocument()
  })

  it('見出しの隣に全話者の声を生成ボタンを表示する', () => {
    renderContent()
    expect(screen.getByRole('button', { name: '全話者の声を生成' })).toBeInTheDocument()
  })

  it('登場人物見出しと一括生成は右ペインの先頭に固定する', () => {
    renderContent()

    expect(screen.getByRole('heading', { name: '登場人物' }).closest('.sticky')).toHaveClass('sticky', 'top-0')
  })

  it('全話者の声を生成中は現在の話者と進行数を表示する', async () => {
    let completeNarrator: ((value: { speaker: string; voice_id: string; voice_traits: string }) => void) | undefined
    vi.mocked(createVoice).mockImplementationOnce(() => new Promise((resolve) => { completeNarrator = resolve }))
    vi.mocked(createVoice).mockResolvedValueOnce({ speaker: 'Alice', voice_id: 'voice-alice', voice_traits: '勇敢な少女声' })
    const user = userEvent.setup()
    renderContent()

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    expect(await screen.findByRole('status')).toHaveTextContent('ナレーターの声を生成中（1/2）')
    completeNarrator?.({ speaker: 'ナレーター', voice_id: 'voice-narrator', voice_traits: '知的な語り手' })
    await waitFor(() => expect(createVoice).toHaveBeenCalledTimes(2))
  })

  it('全話者の声を生成は割当済みの声を再生成しない', async () => {
    vi.mocked(createVoice).mockResolvedValueOnce({ speaker: 'ナレーター', voice_id: 'voice-narrator', voice_traits: '知的な語り手' })
    const user = userEvent.setup()
    renderContent(documentWithVoice())

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    await waitFor(() => expect(createVoice).toHaveBeenCalledTimes(1))
    expect(createVoice).toHaveBeenCalledWith('doc-test-1', { speaker: 'ナレーター', voice_traits: '知的な語り手' })
    expect(createVoice).not.toHaveBeenCalledWith('doc-test-1', { speaker: 'Alice', voice_traits: '勇敢な少女声' })
  })

  it('脚本で発話しない分析上の人物は一括生成と再生前チェックの対象にしない', async () => {
    vi.mocked(createVoice)
      .mockResolvedValueOnce({ speaker: 'ナレーター', voice_id: 'voice-narrator', voice_traits: '知的な語り手' })
      .mockResolvedValueOnce({ speaker: 'Alice', voice_id: 'voice-alice', voice_traits: '勇敢な少女声' })
    const user = userEvent.setup()
    renderContent({
      ...mockDocument,
      analysis: {
        ...mockDocument.analysis,
        characters: [
          ...mockDocument.analysis.characters,
          { ...mockDocument.analysis.characters[0], name: '言及だけの人物', voice_traits: '静かな声' },
        ],
      },
    })

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    await waitFor(() => expect(createVoice).toHaveBeenCalledTimes(2))
    expect(createVoice).not.toHaveBeenCalledWith('doc-test-1', { speaker: '言及だけの人物', voice_traits: '静かな声' })
  })

  it('キャラクター詳細を展開すると voice_traits の行を表示する', async () => {
    const user = userEvent.setup()
    renderContent()

    await user.click(screen.getByLabelText('Aliceを開く'))

    expect(screen.getByText('勇敢な少女声')).toBeInTheDocument()
  })

  it('ナレーターを割り当て可能な話者として表示し、voice_traits を出す', async () => {
    const user = userEvent.setup()
    renderContent()

    const narratorToggle = screen.getByLabelText('ナレーターを開く')
    expect(narratorToggle).toBeInTheDocument()

    await user.click(narratorToggle)
    expect(screen.getByText('知的な語り手')).toBeInTheDocument()
  })

  it('分析に含まれないが脚本で発話する話者も表示する', async () => {
    const user = userEvent.setup()
    const onDraftChange = vi.fn()
    renderContent({
      ...mockDocument,
      source_fountain: `${FOUNTAIN}\n\n@専門の猟師\nここは危険だ。`,
    }, onDraftChange)

    expect(screen.getByText('専門の猟師')).toBeInTheDocument()
    await user.click(screen.getByLabelText('専門の猟師を開く'))
    expect(screen.queryByText('自然な話し方の声')).not.toBeInTheDocument()
    expect(screen.queryByText('外的目標')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add to Characters' }))
    expect(onDraftChange).toHaveBeenCalledWith(expect.objectContaining({
      analysis: expect.objectContaining({ characters: expect.arrayContaining([expect.objectContaining({ name: '専門の猟師' })]) }),
      emotion_arc: expect.objectContaining({ characters: expect.objectContaining({ '専門の猟師': [0] }) }),
    }))
  })

  it('キャラクター追加は人物と arc のキーだけを draft に加える', async () => {
    const onDraftChange = vi.fn()
    const user = userEvent.setup()
    const turningPoints = [{
      availability: 'identified' as const,
      tp_number: 1,
      label: '機会',
      scene_number: 1,
      change: '関係の変化',
      involved_characters: [{ name: '履歴人物', goal: '望み', conflict: '対立', choice: '選択', action: '行動', change: '変化' }],
    }]
    const document = {
      ...mockDocument,
      analysis: { ...mockDocument.analysis, turning_points: turningPoints },
    }
    renderContent(document, onDraftChange)

    await user.click(screen.getByRole('button', { name: 'キャラクターを追加' }))
    await user.type(screen.getByRole('textbox', { name: 'キャラクター名' }), 'New Character')
    await user.click(screen.getByRole('button', { name: '追加' }))

    const update = onDraftChange.mock.calls[0][0]
    expect(update.analysis.characters.map((character: { name: string }) => character.name)).toEqual(['Alice', 'New Character'])
    expect(update.emotion_arc.characters).toEqual({ 'New Character': [0] })
    expect(update.analysis.turning_points).toEqual(turningPoints)
    expect(screen.queryByText(/追加したキャラクターの Emotional arc/)).not.toBeInTheDocument()
  })

  it('空名・重複名の追加と改名を受け付けない', async () => {
    const onDraftChange = vi.fn()
    const user = userEvent.setup()
    const document: DocumentDetail = {
      ...mockDocument,
      analysis: {
        ...mockDocument.analysis,
        characters: [
          ...mockDocument.analysis.characters,
          { ...mockDocument.analysis.characters[0], name: 'Bob' },
        ],
      },
      emotion_arc: { ...mockDocument.emotion_arc, characters: { Alice: [4], Bob: [2] } },
    }
    renderContent(document, onDraftChange)

    await user.click(screen.getByRole('button', { name: 'キャラクターを追加' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '追加' }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'キャラクター名' }), 'Bob')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '追加' }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(onDraftChange).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'キャンセル' }))

    await user.click(screen.getByLabelText('Aliceを開く'))
    await user.click(screen.getByLabelText('Aliceを編集'))
    const editor = screen.getByRole('dialog')
    const name = within(editor).getByRole('textbox', { name: 'キャラクター名' })
    await user.clear(name)
    await user.click(within(editor).getByRole('button', { name: 'OK' }))
    expect(within(editor).getByRole('alert')).toBeInTheDocument()
    await user.type(name, 'Bob')
    await user.click(within(editor).getByRole('button', { name: 'OK' }))
    expect(within(editor).getByRole('alert')).toBeInTheDocument()
    expect(onDraftChange).not.toHaveBeenCalled()
  })

  it('改名は arc の既存値を移し、転換点を変えない', async () => {
    const onDraftChange = vi.fn()
    const turningPoints = [{
      availability: 'identified' as const,
      tp_number: 1,
      label: '機会',
      scene_number: 1,
      change: '関係の変化',
      involved_characters: [{ name: 'Alice', goal: '望み', conflict: '対立', choice: '選択', action: '行動', change: '変化' }],
    }]
    const document: DocumentDetail = {
      ...mockDocument,
      analysis: { ...mockDocument.analysis, turning_points: turningPoints },
      emotion_arc: { ...mockDocument.emotion_arc, characters: { Alice: [5] } },
    }
    const user = userEvent.setup()
    renderContent(document, onDraftChange)
    await user.click(screen.getByLabelText('Aliceを開く'))
    await user.click(screen.getByLabelText('Aliceを編集'))
    const dialog = screen.getByRole('dialog')
    await user.clear(within(dialog).getByRole('textbox', { name: 'キャラクター名' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'キャラクター名' }), 'Renamed Alice')
    await user.click(within(dialog).getByRole('button', { name: 'OK' }))

    const update = onDraftChange.mock.calls[0][0]
    expect(update.analysis.characters.map((character: { name: string }) => character.name)).toContain('Renamed Alice')
    expect(update.emotion_arc.characters).toEqual({ 'Renamed Alice': [5] })
    expect(update.analysis.turning_points).toEqual(turningPoints)
  })

  it('キャラクター削除は同名のプロフィールと arc キーだけを draft から除く', async () => {
    const onDraftChange = vi.fn()
    const user = userEvent.setup()
    const turningPoints = [{
      availability: 'identified' as const,
      tp_number: 1,
      label: '機会',
      scene_number: 1,
      change: '関係の変化',
      involved_characters: [{ name: 'Alice', goal: '望み', conflict: '対立', choice: '選択', action: '行動', change: '変化' }],
    }]
    const document: DocumentDetail = {
      ...mockDocument,
      analysis: { ...mockDocument.analysis, turning_points: turningPoints, characters: [...mockDocument.analysis.characters, { ...mockDocument.analysis.characters[0], name: 'Bob' }] },
      emotion_arc: { ...mockDocument.emotion_arc, characters: { Alice: [4], Bob: [0] } },
    }
    renderContent(document, onDraftChange)

    await user.click(screen.getByRole('button', { name: 'Bob を Characters から削除' }))
    await user.click(screen.getByRole('button', { name: '削除' }))

    const update = onDraftChange.mock.calls[0][0]
    expect(update.analysis.characters.map((character: { name: string }) => character.name)).toEqual(['Alice'])
    expect(update.emotion_arc.characters).toEqual({ Alice: [4] })
    expect(update.analysis.turning_points).toEqual(turningPoints)
  })
})

describe('AnalysisContent 編集ダイアログの声コントロール', () => {
  async function openCharacterDialog(document: DocumentDetail = mockDocument) {
    const user = userEvent.setup()
    renderContent(document)
    await user.click(screen.getByLabelText('Aliceを開く'))
    await user.click(screen.getByLabelText('Aliceを編集'))
    return { user, dialog: screen.getByRole('dialog') }
  }

  it('特徴は常時表示（Textarea）で、他フィールドは閉じている', async () => {
    const { dialog } = await openCharacterDialog()
    expect(within(dialog).getByLabelText('Alice 特徴')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Alice 外的目標')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Alice 内的欲求')).not.toBeInTheDocument()
  })

  it('チェック用文章は演技指示でなく最初のセリフを編集可能に表示し、声がなければ再生は無効', async () => {
    const { dialog } = await openCharacterDialog()
    const checkText = within(dialog).getByRole('textbox', { name: 'Alice チェック用文章' })
    expect(checkText).toHaveValue('I will save this town.')
    expect(within(dialog).getByRole('button', { name: 'Alice 再生' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Alice 生成' })).toBeEnabled()
  })

  it('声が既にあれば再生は有効で、previewVoice と再生を呼ぶ', async () => {
    vi.mocked(previewVoice).mockResolvedValue(new Uint8Array([1, 2, 3, 4]))
    const { user, dialog } = await openCharacterDialog(documentWithVoice())
    const playButton = within(dialog).getByRole('button', { name: 'Alice 再生' })
    expect(playButton).toBeEnabled()

    await user.click(playButton)

    await waitFor(() => expect(previewVoice).toHaveBeenCalledWith('doc-test-1', { voice_id: 'voice-abc', text: 'I will save this town.' }))
    await waitFor(() => expect(playPcm16).toHaveBeenCalled())
  })

  it('編集したチェック用文章で試聴する', async () => {
    vi.mocked(previewVoice).mockResolvedValue(new Uint8Array([1, 2, 3, 4]))
    const { user, dialog } = await openCharacterDialog(documentWithVoice())
    const checkText = within(dialog).getByRole('textbox', { name: 'Alice チェック用文章' })

    await user.clear(checkText)
    await user.type(checkText, 'Custom line.')
    await user.click(within(dialog).getByRole('button', { name: 'Alice 再生' }))

    await waitFor(() => expect(previewVoice).toHaveBeenCalledWith('doc-test-1', { voice_id: 'voice-abc', text: 'Custom line.' }))
  })

  it('生成ボタンで createVoice を呼び、割当を下書きへ反映する', async () => {
    vi.mocked(createVoice).mockResolvedValue({ speaker: 'Alice', voice_id: 'voice-new', voice_traits: '勇敢な少女声' })
    const onDraftChange = vi.fn()
    const user = userEvent.setup()
    renderContent(mockDocument, onDraftChange)
    await user.click(screen.getByLabelText('Aliceを開く'))
    await user.click(screen.getByLabelText('Aliceを編集'))
    const dialog = screen.getByRole('dialog')

    await user.click(within(dialog).getByRole('button', { name: 'Alice 生成' }))

    await waitFor(() => expect(createVoice).toHaveBeenCalledWith('doc-test-1', { speaker: 'Alice', voice_traits: '勇敢な少女声' }))
    await waitFor(() => expect(onDraftChange).toHaveBeenCalledWith({ voice_assignments: [{ speaker: 'Alice', voice_id: 'voice-new', voice_traits: '勇敢な少女声' }] }))
  })

  it('個別の声生成中は対象話者を表示する', async () => {
    let complete: ((value: { speaker: string; voice_id: string; voice_traits: string }) => void) | undefined
    vi.mocked(createVoice).mockImplementationOnce(() => new Promise((resolve) => { complete = resolve }))
    const { user, dialog } = await openCharacterDialog()

    await user.click(within(dialog).getByRole('button', { name: 'Alice 生成' }))

    expect(await within(dialog).findByRole('status')).toHaveTextContent('Aliceの声を生成中…')
    complete?.({ speaker: 'Alice', voice_id: 'voice-new', voice_traits: '勇敢な少女声' })
    await waitFor(() => expect(createVoice).toHaveBeenCalledTimes(1))
  })

  it('一括生成が途中で失敗しても完了済みの声を下書きに残す', async () => {
    vi.mocked(createVoice)
      .mockResolvedValueOnce({ speaker: 'ナレーター', voice_id: 'voice-narrator', voice_traits: '知的な語り手' })
      .mockRejectedValueOnce(new ApiClientError(502, 'VALIDATION_ERROR', 'provider detail'))
    const onDraftChange = vi.fn()
    const user = userEvent.setup()
    renderContent(mockDocument, onDraftChange)

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    await waitFor(() => expect(onDraftChange).toHaveBeenCalledWith({
      voice_assignments: [{ speaker: 'ナレーター', voice_id: 'voice-narrator', voice_traits: '知的な語り手' }],
    }))
    expect(await screen.findByText('声を作成できませんでした。声の特徴とGemini APIの設定を確認して、再試行してください。')).toBeInTheDocument()
  })
})

describe('AnalysisContent 声の上限エラー', () => {
  it('createVoice が VOICE_LIMIT_EXCEEDED を投げると上限ダイアログを表示する', async () => {
    vi.mocked(createVoice).mockRejectedValue(new ApiClientError(409, VOICE_LIMIT_EXCEEDED, 'VOICE_LIMIT_EXCEEDED: over'))
    const user = userEvent.setup()
    renderContent()

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    await waitFor(() =>
      expect(
        screen.getByText('作成できる声の上限に達しました。不要な古い声を削除してください。'),
      ).toBeInTheDocument(),
    )
  })

  it('通常の声作成失敗では安全な再試行メッセージを表示する', async () => {
    vi.mocked(createVoice).mockRejectedValue(new ApiClientError(502, 'VALIDATION_ERROR', 'provider detail'))
    const user = userEvent.setup()
    renderContent()

    await user.click(screen.getByRole('button', { name: '全話者の声を生成' }))

    expect(await screen.findByText('声を作成できませんでした。声の特徴とGemini APIの設定を確認して、再試行してください。')).toBeInTheDocument()
  })
})
