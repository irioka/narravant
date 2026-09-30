export type FormattingLocale = 'en' | 'ja'

export const INTL_LOCALE: Record<FormattingLocale, 'en-US' | 'ja-JP'> = {
  en: 'en-US',
  ja: 'ja-JP',
}

export function formatDateTime(
  value: string | number | Date,
  locale: FormattingLocale,
  options: Intl.DateTimeFormatOptions,
): string | undefined {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return undefined
  return new Intl.DateTimeFormat(INTL_LOCALE[locale], options).format(date)
}

export function formatNumber(
  value: number,
  locale: FormattingLocale,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(INTL_LOCALE[locale], options).format(value)
}

export function formatElapsedTime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
