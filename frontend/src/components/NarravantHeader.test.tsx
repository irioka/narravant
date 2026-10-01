import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { NarravantHeader } from './NarravantHeader'

describe('NarravantHeader', () => {
  it('shows the canonical application version beside the brand and keeps actions on the left', () => {
    render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}>
        <NarravantHeader actions={<button type="button">Import</button>} />
      </LocaleProvider>,
    )

    expect(screen.getByText('NARRAVANT')).toBeInTheDocument()
    expect(screen.getByText('v0.1.1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Language' })).toBeInTheDocument()
  })
})
