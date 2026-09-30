import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useTranslation } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createLocaleRuntime, LocaleProvider, useLocale } from './LocaleProvider'

function Probe() {
  const { locale, setLocale } = useLocale()
  const { t } = useTranslation('common')
  return <><output data-testid="locale">{locale}</output><output data-testid="translated">{t('actions.save')}</output><button onClick={() => setLocale('ja')}>switch</button></>
}

describe('LocaleProvider', () => {
  afterEach(() => cleanup())

  it('uses a valid saved locale, updates html lang, and persists a selection', async () => {
    const storage = { getItem: vi.fn(() => 'ja'), setItem: vi.fn() }
    const user = userEvent.setup()
    render(<LocaleProvider runtime={createLocaleRuntime({ storage })}><Probe /></LocaleProvider>)

    expect(screen.getByTestId('locale')).toHaveTextContent('ja')
    expect(screen.getByTestId('translated')).toHaveTextContent('保存')
    expect(document.documentElement.lang).toBe('ja')
    await user.click(screen.getByRole('button', { name: 'switch' }))
    expect(storage.setItem).not.toHaveBeenCalled()
  })

  it('falls back to English when storage contains an invalid locale', () => {
    const storage = { getItem: vi.fn(() => 'fr'), setItem: vi.fn() }
    render(<LocaleProvider runtime={createLocaleRuntime({ storage })}><Probe /></LocaleProvider>)

    expect(screen.getByTestId('locale')).toHaveTextContent('en')
    expect(screen.getByTestId('translated')).toHaveTextContent('Save')
  })
})
