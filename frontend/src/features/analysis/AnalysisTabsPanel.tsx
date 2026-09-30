import { ChartSpline, FileClock, RotateCw } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { DocumentDetail, TurningPoint } from '@/api/contracts'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AnalysisEditorDialog, TurningPointSummary, type AnalysisDraftUpdate, type EditableSection } from './AnalysisContent'
import { EmotionArcChart } from './EmotionArcChart'

export interface AnalysisTabsPanelProps {
  activeSceneNumber?: number
  document: DocumentDetail
  onDraftChange?: (update: AnalysisDraftUpdate) => void
  onOpenSimilarities?: () => void
  onReanalyze?: () => void
  onSceneSelect?: (sceneNumber: number) => void
  onSelectVersion: (versionId: number) => void
  titleMode: 'editable' | 'readonly'
  versions: Array<{ version_id: number; created_at: string }>
}

export function AnalysisTabsPanel({ activeSceneNumber, document, onDraftChange, onOpenSimilarities, onReanalyze, onSceneSelect, onSelectVersion, titleMode, versions }: AnalysisTabsPanelProps) {
  const { t } = useTranslation(['analysis', 'common'])
  const [editor, setEditor] = useState<EditableSection>()
  const [synopsisDraft, setSynopsisDraft] = useState('')
  const [turningPointIndex, setTurningPointIndex] = useState<number>()
  const [turningPointsDraft, setTurningPointsDraft] = useState<TurningPoint[]>(document.analysis.turning_points)
  const canEdit = titleMode === 'editable' && Boolean(onDraftChange)
  const synopsis = String(document.metadata.synopsis ?? '')
  const formatVersionDate = (value: string) => new Intl.DateTimeFormat('ja-JP', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))

  const openSynopsisEditor = () => { setSynopsisDraft(synopsis); setEditor('synopsis') }
  const openTurningPointEditor = (index: number) => {
    const tp = document.analysis.turning_points[index]
    if (!tp || tp.availability === 'not_applicable') return
    setTurningPointsDraft(document.analysis.turning_points)
    setTurningPointIndex(index)
    setEditor('turning_point')
  }
  const apply = () => {
    if (editor === 'synopsis') onDraftChange?.({ metadata: { ...document.metadata, synopsis: synopsisDraft } })
    if (editor === 'turning_point' && turningPointIndex !== undefined) onDraftChange?.({ analysis: { ...document.analysis, turning_points: turningPointsDraft } })
    setEditor(undefined)
    setTurningPointIndex(undefined)
  }

  return <section className="flex h-full flex-col bg-card"><Tabs className="flex h-full flex-col" defaultValue="history"><div className="flex h-10 shrink-0 items-center border-b px-2"><TabsList className="h-7"><TabsTrigger className="gap-1.5 text-xs" value="history">{t('analysis:tabs.history')}</TabsTrigger><TabsTrigger className="gap-1.5 text-xs" value="synopsis">{t('analysis:tabs.synopsis')}</TabsTrigger><TabsTrigger className="gap-1.5 text-xs" value="emotionalArc">{t('analysis:tabs.emotionalArc')}</TabsTrigger><TabsTrigger className="gap-1.5 text-xs" value="turningPoints">{t('analysis:tabs.turningPoints')}</TabsTrigger></TabsList></div>
    <TabsContent className="mt-0 min-h-0 flex-1" value="history"><ScrollArea className="h-full"><div className="space-y-1 p-2">{versions.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t('analysis:history.noVersions')}</p>}{versions.map((version) => <Button className="w-full justify-start gap-2" key={version.version_id} onClick={() => onSelectVersion(version.version_id)} size="sm" variant={version.version_id === document.version_id ? 'secondary' : 'ghost'}><FileClock className="size-4" />v{version.version_id} · {formatVersionDate(version.created_at)}</Button>)}</div></ScrollArea></TabsContent>
    <TabsContent className="mt-0 min-h-0 flex-1" value="synopsis"><ScrollArea className="h-full"><div className="p-3"><section aria-label={t('analysis:sections.synopsis')} className="rounded-md border bg-background p-3">{canEdit ? <button className="block w-full text-left" onClick={openSynopsisEditor} type="button"><p className="text-sm leading-6 text-muted-foreground">{synopsis || t('analysis:empty.synopsis')}</p></button> : <p className="text-sm leading-6 text-muted-foreground">{synopsis || t('analysis:empty.synopsis')}</p>}</section></div></ScrollArea></TabsContent>
    <TabsContent className="mt-0 min-h-0 flex-1" value="emotionalArc"><ScrollArea className="h-full"><div className="p-3"><EmotionArcChart activeSceneNumber={activeSceneNumber} arc={document.emotion_arc} onSceneSelect={onSceneSelect} scenes={document.scenes} turningPointSceneNumbers={document.analysis.turning_points.flatMap((point) => point.availability === 'identified' ? [point.scene_number] : [])} />{(onOpenSimilarities || onReanalyze) && <div className={`mt-3 grid gap-2 ${onOpenSimilarities && onReanalyze ? 'grid-cols-2' : 'grid-cols-1'}`}>{onOpenSimilarities && <Button onClick={onOpenSimilarities} size="sm" variant="outline"><ChartSpline />{t('analysis:chart.valenceSimilarity')}</Button>}{onReanalyze && <Button onClick={onReanalyze} size="sm" variant="outline"><RotateCw />{t('common:actions.reanalyze')}</Button>}</div>}</div></ScrollArea></TabsContent>
    <TabsContent className="mt-0 min-h-0 flex-1" value="turningPoints"><ScrollArea className="h-full"><div className="space-y-2 p-3">{document.analysis.turning_points.length === 0 && <p className="text-sm text-muted-foreground">{t('analysis:empty.turningPoints')}</p>}{document.analysis.turning_points.map((tp, index) => <TurningPointSummary canEdit={canEdit} key={tp.tp_number} onEdit={() => openTurningPointEditor(index)} tp={tp} />)}</div></ScrollArea></TabsContent>
  </Tabs>
    <AnalysisEditorDialog canEdit={canEdit} character={undefined} documentId={document.document_id} isDraft={false} editor={editor} narratorTraits="" onCharacterChange={() => { }} onNarratorChange={() => { }} onOpenChange={(open) => { if (!open) { setEditor(undefined); setTurningPointIndex(undefined) } }} onSave={apply} onSynopsisChange={setSynopsisDraft} onTurningPointChange={(value) => turningPointIndex !== undefined && setTurningPointsDraft(turningPointsDraft.map((item, index) => index === turningPointIndex ? value : item))} onVoiceGenerated={() => { }} onVoiceLimitExceeded={() => { }} sourceFountain={document.source_fountain} synopsis={synopsisDraft} tp={turningPointIndex === undefined ? undefined : turningPointsDraft[turningPointIndex]} voiceAssignments={document.voice_assignments ?? []} />
  </section>
}
