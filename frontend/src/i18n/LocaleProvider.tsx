import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { i18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { toast } from 'sonner'

import { localizeUiError } from './errors'
import { createNarravantI18n } from './i18n'
import { DEFAULT_LOCALE, isLocale, LOCALE_STORAGE_KEY, type Locale, type LocaleStorage, resolveInitialLocale } from './locale'

export interface LocaleRuntime {
  i18n: i18n
  initialLocale: Locale
  storage?: LocaleStorage
  storageReadFailed: boolean
}

interface LocaleProviderProps {
  children: ReactNode
  runtime?: LocaleRuntime
}

interface LocaleContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
}

const LocaleContext = createContext<LocaleContextValue | undefined>(undefined)

function getBrowserStorage(): LocaleStorage {
  return window.localStorage
}

export function createLocaleRuntime(options: { initialLocale?: Locale; storage?: LocaleStorage } = {}): LocaleRuntime {
  let storage = options.storage
  let bootstrap = { locale: options.initialLocale ?? DEFAULT_LOCALE, storageReadFailed: false }

  if (options.initialLocale === undefined) {
    bootstrap = resolveInitialLocale(() => storage ?? getBrowserStorage())
  }

  if (!storage) {
    try {
      storage = getBrowserStorage()
    } catch {
      storage = undefined
    }
  }

  return {
    i18n: createNarravantI18n(bootstrap.locale),
    initialLocale: bootstrap.locale,
    storage,
    storageReadFailed: bootstrap.storageReadFailed,
  }
}

export function LocaleProvider({ children, runtime = createLocaleRuntime() }: LocaleProviderProps) {
  const [locale, setLocaleState] = useState(runtime.initialLocale)
  const storageReadNotified = useRef(false)

  const applyLocale = useCallback((nextLocale: Locale) => {
    void runtime.i18n.changeLanguage(nextLocale)
    document.documentElement.lang = nextLocale
    setLocaleState(nextLocale)
  }, [runtime.i18n])

  const setLocale = useCallback((nextLocale: Locale) => {
    if (nextLocale === locale) return

    applyLocale(nextLocale)
    try {
      if (!runtime.storage) throw new Error('localStorage is unavailable')
      runtime.storage.setItem(LOCALE_STORAGE_KEY, nextLocale)
    } catch {
      const translate = runtime.i18n.getFixedT(nextLocale, 'errors')
      toast.error(localizeUiError(translate, { code: 'STORAGE_WRITE_FAILED' }).message)
    }
  }, [applyLocale, locale, runtime.i18n, runtime.storage])

  useEffect(() => {
    void runtime.i18n.changeLanguage(locale)
    document.documentElement.lang = locale
  }, [locale, runtime.i18n])

  useEffect(() => {
    if (!runtime.storageReadFailed || storageReadNotified.current) return
    storageReadNotified.current = true
    const translate = runtime.i18n.getFixedT(runtime.initialLocale, 'errors')
    toast.error(localizeUiError(translate, { code: 'STORAGE_READ_FAILED' }).message)
  }, [runtime.i18n, runtime.initialLocale, runtime.storageReadFailed])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== LOCALE_STORAGE_KEY || !isLocale(event.newValue)) return
      applyLocale(event.newValue)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [applyLocale])

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale])
  return <I18nextProvider i18n={runtime.i18n}><LocaleContext.Provider value={value}>{children}</LocaleContext.Provider></I18nextProvider>
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext)
  if (!value) throw new Error('useLocale must be used within LocaleProvider')
  return value
}
