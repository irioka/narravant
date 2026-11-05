import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Group, Panel, Separator, type Layout } from 'react-resizable-panels'

const DEFAULT_LAYOUT: Layout = {
  'workbench-list': 30,
  'workbench-script': 40,
  'workbench-characters': 30,
}

// Keep resize state separate from the document list, editor, and character views.
export function WorkbenchPanels({ left, script, characters }: {
  left: ReactNode
  script: ReactNode
  characters: ReactNode
}) {
  const { t } = useTranslation('analysis')
  const [layout, setLayout] = useState(DEFAULT_LAYOUT)
  const percent = (id: string) => `${Math.round(layout[id] ?? DEFAULT_LAYOUT[id])}%`

  return <div className="workbench-panels min-h-0 flex-1">
    <Group className="h-full" onLayoutChange={setLayout} orientation="horizontal">
      <Panel id="workbench-list" defaultSize="30%" minSize="24%">{left}</Panel>
      <Separator aria-label={t('layout.resizeListScript')} aria-describedby="workbench-list-script-ratios" className="relative w-px bg-border hover:bg-primary/50">
        <output id="workbench-list-script-ratios" aria-label={t('layout.widths')} aria-live="off" className="workbench-panel-ratios pointer-events-none absolute left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 items-center gap-4 whitespace-nowrap rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md tabular-nums">
          <span>{t('layout.list')} {percent('workbench-list')}</span>
          <span>{t('layout.script')} {percent('workbench-script')}</span>
        </output>
      </Separator>
      <Panel id="workbench-script" defaultSize="40%" minSize="30%">{script}</Panel>
      <Separator aria-label={t('layout.resizeScriptCharacters')} aria-describedby="workbench-script-characters-ratios" className="relative w-px bg-border hover:bg-primary/50">
        <output id="workbench-script-characters-ratios" aria-label={t('layout.widths')} aria-live="off" className="workbench-panel-ratios pointer-events-none absolute left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 items-center gap-4 whitespace-nowrap rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md tabular-nums">
          <span>{t('layout.script')} {percent('workbench-script')}</span>
          <span>{t('layout.characters')} {percent('workbench-characters')}</span>
        </output>
      </Separator>
      <Panel id="workbench-characters" defaultSize="30%" minSize="18%">{characters}</Panel>
    </Group>
  </div>
}
