import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getPlaybackSettings } from '@/api/playback'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { PlaybackSettingsDialog } from './PlaybackSettingsDialog'

vi.mock('@/api/playback', async (original) => ({ ...await original<typeof import('@/api/playback')>(), getPlaybackSettings: vi.fn() }))
const defaults = { scene_pause_duration_ms: 1700, speaker_change_pause_duration_ms: 600, narrator_sentence_pause_duration_ms: 800, min_ms: 0 as const, max_ms: 60000 as const, step_ms: 100 as const }
afterEach(cleanup)
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getPlaybackSettings).mockResolvedValue(defaults) })
function show(props: Partial<React.ComponentProps<typeof PlaybackSettingsDialog>> = {}, initialLocale: 'ja' | 'en' = 'en') {
  const onConfirm = vi.fn(), onOpenChange = vi.fn()
  render(<LocaleProvider runtime={createLocaleRuntime({ initialLocale })}><PlaybackSettingsDialog open onConfirm={onConfirm} onOpenChange={onOpenChange} {...props} /></LocaleProvider>)
  return { onConfirm, onOpenChange }
}

it('uses server values and converts 1.2 sec and zero to integer milliseconds', async () => {
  const { onConfirm } = show()
  const scene = await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })
  expect(scene).toHaveValue(1.7)
  fireEvent.change(scene, { target: { value: '1.2' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Speaker change interval (sec)' }), { target: { value: '0' } })
  fireEvent.click(screen.getByRole('button', { name: 'OK' }))
  expect(onConfirm).toHaveBeenCalledWith({ scene_pause_duration_ms: 1200, speaker_change_pause_duration_ms: 0, narrator_sentence_pause_duration_ms: 800 })
})

it.each(['', '-0.1', '60.1', '1.23'])('rejects invalid seconds %s', async (value) => {
  show()
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' }), { target: { value } })
  expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled()
})

it('leaves defaults unspecified; Reset only changes the confirmed override on OK', async () => {
  const { onConfirm } = show()
  await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })
  fireEvent.click(screen.getByRole('button', { name: 'OK' }))
  expect(onConfirm).toHaveBeenCalledWith(undefined)
})

it('discards Reset on Cancel and confirms it as omitted values on OK', async () => {
  const { onConfirm, onOpenChange } = show({ overrides: { ...defaults, scene_pause_duration_ms: 1200 } })
  expect(await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })).toHaveValue(1.2)
  fireEvent.click(screen.getByRole('button', { name: 'Restore defaults' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onConfirm).not.toHaveBeenCalled()
  expect(onOpenChange).toHaveBeenCalledWith(false)
  fireEvent.click(screen.getByRole('button', { name: 'OK' }))
  expect(onConfirm).toHaveBeenCalledWith(undefined)
})

it('shows fetch errors and allows an explicit reload', async () => {
  vi.mocked(getPlaybackSettings).mockRejectedValueOnce(new Error('Synthetic failure'))
  show()
  expect(screen.queryByRole('button', { name: 'Reload settings' })).not.toBeInTheDocument()
  await screen.findByRole('alert')
  expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Reload settings' }))
  expect(screen.queryByRole('button', { name: 'Reload settings' })).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Scene interval (sec)' })).toHaveValue(1.7))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Reload settings' })).not.toBeInTheDocument()
})

it('adopts rounded non-step server values only on OK and never clamps an out-of-range config', async () => {
  vi.mocked(getPlaybackSettings).mockResolvedValue({ ...defaults, scene_pause_duration_ms: 1250 })
  const { onConfirm } = show()
  expect(await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })).toHaveValue(1.3)
  fireEvent.click(screen.getByRole('button', { name: 'OK' }))
  expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ scene_pause_duration_ms: 1300 }))
})


it('keeps out-of-range server values visible and allows Reset without clamping', async () => {
  vi.mocked(getPlaybackSettings).mockResolvedValue({ ...defaults, scene_pause_duration_ms: 65000 })
  const { onConfirm } = show()
  expect(await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })).toHaveValue(65)
  expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Restore defaults' }))
  fireEvent.click(screen.getByRole('button', { name: 'OK' }))
  expect(onConfirm).toHaveBeenCalledWith(undefined)
})

it('keeps an existing override after fetch failure and Cancel', async () => {
  const overrides = { ...defaults, scene_pause_duration_ms: 1200 }
  vi.mocked(getPlaybackSettings).mockRejectedValueOnce(new Error('Synthetic failure'))
  const { onConfirm, onOpenChange } = show({ overrides })
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onConfirm).not.toHaveBeenCalled()
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(overrides.scene_pause_duration_ms).toBe(1200)
})

it('hides Reload while loading and after a successful initial fetch', async () => {
  show()
  expect(screen.queryByRole('button', { name: 'Reload settings' })).not.toBeInTheDocument()
  await screen.findByRole('spinbutton', { name: 'Scene interval (sec)' })
  expect(screen.queryByRole('button', { name: 'Reload settings' })).not.toBeInTheDocument()
})

it('labels the Japanese error recovery button 再取得', async () => {
  vi.mocked(getPlaybackSettings).mockRejectedValueOnce(new Error('Synthetic failure'))
  show({}, 'ja')
  expect(await screen.findByRole('alert')).toHaveTextContent('設定を取得できませんでした。再取得してください。')
  expect(screen.getByRole('button', { name: '再取得' })).toBeEnabled()
})
