export const SUPPORTED_LOCALES = ['en', 'ja'] as const

export type Locale = (typeof SUPPORTED_LOCALES)[number]

export const DEFAULT_LOCALE: Locale = 'en'
export const LOCALE_STORAGE_KEY = 'narravant.locale'

export interface LocaleBootstrap {
  locale: Locale
  storageReadFailed: boolean
}

export type LocaleStorage = Pick<Storage, 'getItem' | 'setItem'>

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && SUPPORTED_LOCALES.includes(value as Locale)
}

export function resolveInitialLocale(getStorage: () => Pick<Storage, 'getItem'>): LocaleBootstrap {
  try {
    const storedLocale = getStorage().getItem(LOCALE_STORAGE_KEY)
    return { locale: isLocale(storedLocale) ? storedLocale : DEFAULT_LOCALE, storageReadFailed: false }
  } catch {
    return { locale: DEFAULT_LOCALE, storageReadFailed: true }
  }
}
