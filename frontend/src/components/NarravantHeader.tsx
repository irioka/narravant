import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { LanguageToggle } from '@/i18n/LanguageToggle'
import { APP_VERSION } from '@/version'

interface NarravantHeaderProps {
  actions?: ReactNode
}

export function NarravantHeader({ actions }: NarravantHeaderProps) {
  const { t } = useTranslation('common')

  return (
    <header className="grid h-10 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 border-b bg-card px-3">
      <div className="flex min-w-0 items-center gap-1">{actions}</div>
      <div className="flex items-baseline justify-center gap-1.5">
        <span className="font-brand text-xl font-semibold tracking-[0.01em]">{t('app.brand')}</span>
        <span className="text-[10px] font-medium tracking-normal text-muted-foreground">v{APP_VERSION}</span>
      </div>
      <div className="flex justify-end"><LanguageToggle /></div>
    </header>
  )
}
