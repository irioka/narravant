import { VoiceLibraryProvider } from '@/features/analysis/VoiceLibraryProvider'
import { render, screen } from '@testing-library/react'
import { cleanup, fireEvent, waitFor } from '@testing-library/react'
import { APP_VERSION } from '@/version'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { NarravantHeader } from './NarravantHeader'

vi.mock('@/api/voices', () => ({ listVoices: vi.fn().mockResolvedValue({ voices: [], complete: true }) }))
afterEach(cleanup)

describe('NarravantHeader', () => {
  it('shows the canonical application version beside the brand and keeps actions on the left', () => {
    render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}><VoiceLibraryProvider>
        <NarravantHeader actions={<button type="button">Import</button>} />
      </VoiceLibraryProvider></LocaleProvider>,
    )

    expect(screen.getByText('NARRAVANT')).toBeInTheDocument()
    const version = screen.getByText(`v${APP_VERSION}`)
    const language = screen.getByRole('group', { name: 'Language' })
    expect(version.compareDocumentPosition(language) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(version.parentElement).not.toContainElement(language)
    expect(language.parentElement?.parentElement).toContainElement(screen.getByRole('button', { name: 'Manage Voices' }))
    expect(screen.getByRole('button', { name: 'Manage Voices' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Language' })).toBeInTheDocument()
  })
  it('opens management without a document and restores focus after close', async () => {
    render(<LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}><VoiceLibraryProvider><NarravantHeader /></VoiceLibraryProvider></LocaleProvider>)
    const trigger = screen.getByRole('button', { name: '声を管理' })
    expect(trigger).toHaveAttribute('title', '声を管理')
    expect(screen.getByRole('group', { name: '言語' })).toBeInTheDocument()
    trigger.focus()
    fireEvent.click(trigger)
    expect(await screen.findByRole('dialog', { name: 'Manage Voices' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '閉じる' }))
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})
