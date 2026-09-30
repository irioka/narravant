import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { LanguageToggle } from './LanguageToggle'
import { createLocaleRuntime, LocaleProvider } from './LocaleProvider'

describe('LanguageToggle', () => {
  it('exposes pressed state and changes locale with native button interaction', async () => {
    const user = userEvent.setup()
    render(<LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}><LanguageToggle /></LocaleProvider>)

    const english = screen.getByRole('button', { name: 'Switch to English' })
    const japanese = screen.getByRole('button', { name: 'Switch to Japanese' })
    expect(english).toHaveAttribute('aria-pressed', 'true')
    await user.click(japanese)
    expect(japanese).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '英語に切り替える' })).toHaveAttribute('aria-pressed', 'false')
  })
})
