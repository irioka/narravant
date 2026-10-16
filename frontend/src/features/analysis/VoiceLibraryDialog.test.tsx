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

    await user.click(await screen.findByRole('button', { name: '声を削除' }))
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
    await user.click(screen.getByRole('button', { name: '声を削除' }))
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

    await user.click(await screen.findByRole('button', { name: '声を削除' }))

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
