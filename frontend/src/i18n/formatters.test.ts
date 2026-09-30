import { describe, expect, it } from 'vitest'

import { formatDateTime, formatElapsedTime, formatNumber } from './formatters'

describe('locale-aware formatters', () => {
  it('formats the same date using the selected locale without overriding timezone', () => {
    const value = '2026-09-16T00:00:00Z'
    const options: Intl.DateTimeFormatOptions = { dateStyle: 'short', timeStyle: 'short' }

    expect(formatDateTime(value, 'en', options)).toBe(new Intl.DateTimeFormat('en-US', options).format(new Date(value)))
    expect(formatDateTime(value, 'ja', options)).toBe(new Intl.DateTimeFormat('ja-JP', options).format(new Date(value)))
  })

  it('returns undefined for an invalid date', () => {
    expect(formatDateTime('not-a-date', 'en', { dateStyle: 'short' })).toBeUndefined()
  })

  it('formats numeric values and elapsed time deterministically', () => {
    expect(formatNumber(12_345, 'en')).toBe(new Intl.NumberFormat('en-US').format(12_345))
    expect(formatNumber(92.5, 'ja', { minimumFractionDigits: 2, maximumFractionDigits: 2 })).toBe('92.50')
    expect(formatElapsedTime(65)).toBe('1:05')
  })
})
