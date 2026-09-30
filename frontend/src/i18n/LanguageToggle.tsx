import { useTranslation } from 'react-i18next'

import { type Locale } from './locale'
import { useLocale } from './LocaleProvider'

const options: { locale: Locale; label: string }[] = [
  { locale: 'en', label: 'en' },
  { locale: 'ja', label: 'ja' },
]

export function LanguageToggle() {
  const { t } = useTranslation('common')
  const { locale, setLocale } = useLocale()

  return (
    <div aria-label={t('language.label')} className="inline-flex rounded-md border border-border p-0.5" role="group">
      {options.map((option) => (
        <button
          aria-label={option.locale === 'en' ? t('language.switchToEnglish') : t('language.switchToJapanese')}
          aria-pressed={locale === option.locale}
          className="rounded px-2 py-1 text-xs font-medium text-muted-foreground aria-pressed:bg-muted aria-pressed:text-foreground"
          key={option.locale}
          onClick={() => setLocale(option.locale)}
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
