import { createInstance, type i18n } from 'i18next'
import { initReactI18next } from 'react-i18next'

import { assertCatalogIntegrity, NAMESPACES, resources } from './catalog'

type I18nLocale = 'en' | 'ja'

export function createNarravantI18n(locale: I18nLocale): i18n {
  assertCatalogIntegrity(resources)
  const instance = createInstance()
  void instance.use(initReactI18next).init({
    resources,
    lng: locale,
    supportedLngs: ['en', 'ja'],
    fallbackLng: false,
    defaultNS: 'common',
    ns: NAMESPACES,
    initAsync: false,
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
    returnNull: false,
  })
  if (!instance.isInitialized) throw new Error('i18next did not initialize synchronously')
  return instance
}
