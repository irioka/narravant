import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ComponentProps } from 'react'

import { createVoice, type VoiceSummary } from '@/api/voices'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { deleteVoice, getVoice, getVoiceUsages, listVoices } from '@/api/voices'
import { VoiceLibraryDialog } from './VoiceLibraryDialog'

vi.mock('@/api/voices', () => ({
  createVoice: vi.fn(),
  deleteVoice: vi.fn(),
  getVoice: vi.fn(),
  getVoiceUsages: vi.fn(),
  listVoices: vi.fn(),
}))

const activeVoice: VoiceSummary = {
  voice_id: 'voice_saved_1',
  speaker_label: 'Alice',
  voice_traits: 'warm and clear',
  language_code: 'ja-JP',
  created_at: null,
  created_at_source: 'unknown',
  expire_time: null,
  availability: 'available',
  expiry_state: 'unknown',
  last_checked_at: null,
  usage_count: 1,
}

function renderDialog(props: Partial<ComponentProps<typeof VoiceLibraryDialog>> = {}) {
  return render(
    <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <VoiceLibraryDialog open onOpenChange={() => undefined} {...props} />
    </LocaleProvider>,
  )
}

afterEach(() => cleanup())
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listVoices).mockResolvedValue({ voices: [activeVoice], checked_at: null, complete: true })
  vi.mocked(getVoice).mockResolvedValue(activeVoice)
  vi.mocked(getVoiceUsages).mockResolvedValue({
    usages: [{ document_id: 'doc-a', title: 'Synthetic story', speaker: 'Alice' }],
    complete: true,
  })
  vi.mocked(deleteVoice).mockResolvedValue(undefined)
})

describe('VoiceLibraryDialog', () => {
  it('作成日時と有効期限を秒まで表示する', async () => {
    const voice = {
      ...activeVoice,
      created_at: '2026-10-01T03:54:51Z',
      created_at_source: 'local_response' as const,
      expire_time: '2027-10-01T03:54:51Z',
    }
    vi.mocked(listVoices).mockResolvedValue({ voices: [voice], checked_at: null, complete: true })
    renderDialog()

    expect(await screen.findByRole('listitem')).toHaveTextContent(
      /作成日時: \d{4}\/\d{1,2}\/\d{1,2} \d{2}:\d{2}:\d{2}、有効期限: \d{4}\/\d{1,2}\/\d{1,2} \d{2}:\d{2}:\d{2}/,
    )
  })

  it('期限不明でも存在確認済みの声を選び、Create を呼ばない', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderDialog({ onSelect })

    await user.click(await screen.findByRole('button', { name: '割り当てる' }))

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(activeVoice))
    expect(getVoice).toHaveBeenCalledWith(activeVoice.voice_id)
    expect(listVoices).toHaveBeenCalledTimes(1)
    expect(createVoice).not.toHaveBeenCalled()
  })

  it('利用先を確認してから二重送信を防ぎ、削除後に状態を通知する', async () => {
    const user = userEvent.setup()
    const onVoiceDeleted = vi.fn()
    let finishDelete: (() => void) | undefined
    vi.mocked(deleteVoice).mockImplementationOnce(() => new Promise((resolve) => {
      finishDelete = () => resolve(undefined)
    }))
    renderDialog({ onVoiceDeleted })

    await user.click(await screen.findByRole('checkbox', { name: 'Alice を選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（1件）' }))
    expect(await screen.findAllByText('Synthetic story ; Alice')).toHaveLength(2)
    expect(getVoiceUsages).toHaveBeenCalledWith(activeVoice.voice_id)
    const confirmButton = screen.getByRole('button', { name: '削除' })
    fireEvent.click(confirmButton)
    fireEvent.click(confirmButton)

    await waitFor(() => expect(deleteVoice).toHaveBeenCalledTimes(1))
    expect(confirmButton).toBeDisabled()
    finishDelete?.()
    await waitFor(() => expect(onVoiceDeleted).toHaveBeenCalledWith(activeVoice))
  })

  it('削除後に古い一覧応答が届いても削除済みの声を復活させない', async () => {
    let finishRefresh: ((result: Awaited<ReturnType<typeof listVoices>>) => void) | undefined
    vi.mocked(listVoices)
      .mockResolvedValueOnce({ voices: [activeVoice], checked_at: null, complete: true })
      .mockImplementationOnce(() => new Promise((resolve) => { finishRefresh = resolve }))
    const onVoicesChange = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onVoicesChange })

    expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '再読み込み' }))
    await waitFor(() => expect(listVoices).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('checkbox', { name: 'Alice を選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（1件）' }))
    expect(await screen.findAllByText('Synthetic story ; Alice')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: '削除' }))

    await waitFor(() => expect(onVoicesChange).toHaveBeenLastCalledWith([]))
    await act(async () => {
      finishRefresh?.({ voices: [activeVoice], checked_at: null, complete: true })
    })

    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(onVoicesChange).toHaveBeenLastCalledWith([])
  })

  it('利用先を完全に読めないときは削除確認を出さない', async () => {
    const user = userEvent.setup()
    vi.mocked(getVoiceUsages).mockResolvedValue({ usages: [], complete: false })
    renderDialog()

    await user.click(await screen.findByRole('checkbox', { name: 'Alice を選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（1件）' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('利用先を完全に確認できません')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(deleteVoice).not.toHaveBeenCalled()
  })

  it('全選択した声と利用先をまとめて確認し、キャンセルでは削除しない', async () => {
    const user = userEvent.setup()
    const second = { ...activeVoice, voice_id: 'voice_saved_2', speaker_label: 'Bob' }
    vi.mocked(listVoices).mockResolvedValue({ voices: [activeVoice, second], checked_at: null, complete: true })
    vi.mocked(getVoiceUsages).mockImplementation(async (id) => ({
      usages: [{ document_id: 'doc-a', title: 'Synthetic story', speaker: id === activeVoice.voice_id ? 'Alice' : 'Bob' }],
      complete: true,
    }))
    renderDialog()
    expect(await screen.findByRole('button', { name: '選択した声を削除（0件）' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'すべて選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（2件）' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('Alice')
    expect(dialog).toHaveTextContent('Bob')
    expect(dialog).toHaveTextContent('Synthetic story ; Bob')
    expect(deleteVoice).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'キャンセル' }))
    expect(deleteVoice).not.toHaveBeenCalled()
    expect(screen.getByRole('checkbox', { name: 'Alice を選択' })).toBeChecked()
    await user.click(screen.getByRole('checkbox', { name: 'すべて選択' }))
    expect(screen.getByRole('checkbox', { name: 'Bob を選択' })).not.toBeChecked()
  })

  it('選択した声だけを順番に削除し、途中失敗では成功分を通知して残りの選択を保持する', async () => {
    const user = userEvent.setup()
    const second = { ...activeVoice, voice_id: 'voice_saved_2', speaker_label: 'Bob' }
    const third = { ...activeVoice, voice_id: 'voice_saved_3', speaker_label: 'Carol' }
    const untouched = { ...activeVoice, voice_id: 'voice_saved_4', speaker_label: 'Dave' }
    const onVoiceDeleted = vi.fn()
    const onVoicesChange = vi.fn()
    vi.mocked(listVoices).mockResolvedValue({ voices: [activeVoice, second, third, untouched], checked_at: null, complete: true })
    vi.mocked(deleteVoice).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('synthetic failure'))
    renderDialog({ onVoiceDeleted, onVoicesChange })
    await user.click(await screen.findByRole('checkbox', { name: 'Alice を選択' }))
    await user.click(screen.getByRole('checkbox', { name: 'Bob を選択' }))
    await user.click(screen.getByRole('checkbox', { name: 'Carol を選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（3件）' }))
    await screen.findByRole('alertdialog')
    await user.click(screen.getByRole('button', { name: '削除' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(vi.mocked(deleteVoice).mock.calls.map(([id]) => id)).toEqual([activeVoice.voice_id, second.voice_id])
    expect(onVoiceDeleted).toHaveBeenCalledExactlyOnceWith(activeVoice)
    expect(onVoicesChange).toHaveBeenLastCalledWith([second, third, untouched])
    expect(screen.queryByRole('checkbox', { name: 'Alice を選択' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Bob を選択' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Carol を選択' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Dave を選択' })).not.toBeChecked()
    expect(screen.getByRole('alert')).toHaveTextContent('1件を削除しました')
  })

  it('複数の削除が成功すると選択を解除し、未選択の声を保持する', async () => {
    const user = userEvent.setup()
    const second = { ...activeVoice, voice_id: 'voice_saved_2', speaker_label: 'Bob' }
    const third = { ...activeVoice, voice_id: 'voice_saved_3', speaker_label: 'Carol' }
    const onVoiceDeleted = vi.fn()
    const onVoicesChange = vi.fn()
    const onSelect = vi.fn()
    vi.mocked(listVoices).mockResolvedValue({ voices: [activeVoice, second, third], checked_at: null, complete: true })
    renderDialog({ onVoiceDeleted, onVoicesChange, onSelect })
    await user.click(await screen.findByRole('checkbox', { name: 'Alice を選択' }))
    await user.click(screen.getByRole('checkbox', { name: 'Bob を選択' }))
    expect(screen.getByRole('checkbox', { name: 'すべて選択' })).toBePartiallyChecked()
    expect(onSelect).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '選択した声を削除（2件）' }))
    await screen.findByRole('alertdialog')
    await user.click(screen.getByRole('button', { name: '削除' }))
    await waitFor(() => expect(onVoiceDeleted).toHaveBeenCalledTimes(2))
    expect(vi.mocked(deleteVoice).mock.calls.map(([id]) => id)).toEqual([activeVoice.voice_id, second.voice_id])
    expect(onVoicesChange).toHaveBeenLastCalledWith([third])
    expect(screen.getByRole('checkbox', { name: 'Carol を選択' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '選択した声を削除（0件）' })).toBeDisabled()
  })

  it('選択中の1件でも利用先が不完全なら一括削除を始めない', async () => {
    const user = userEvent.setup()
    const second = { ...activeVoice, voice_id: 'voice_saved_2', speaker_label: 'Bob' }
    vi.mocked(listVoices).mockResolvedValue({ voices: [activeVoice, second], checked_at: null, complete: true })
    vi.mocked(getVoiceUsages).mockImplementation(async (id) => ({ usages: [], complete: id !== second.voice_id }))
    renderDialog()
    await screen.findByRole('heading', { name: 'Alice' })
    await user.click(screen.getByRole('checkbox', { name: 'すべて選択' }))
    await user.click(screen.getByRole('button', { name: '選択した声を削除（2件）' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('利用先を完全に確認できません')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(deleteVoice).not.toHaveBeenCalled()
  })

  it('期限切れの声は割当に選べない', async () => {
    const expired = { ...activeVoice, expiry_state: 'expired' as const }
    vi.mocked(listVoices).mockResolvedValue({ voices: [expired], checked_at: null, complete: true })
    vi.mocked(getVoice).mockResolvedValue(expired)
    renderDialog({ onSelect: vi.fn() })

    expect(await screen.findByRole('button', { name: '割り当てる' })).toBeDisabled()
  })
})
