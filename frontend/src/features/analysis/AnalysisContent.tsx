import { ChevronDown, LoaderCircle, Play, Plus, Sparkles, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { ApiClientError } from '@/api/client'
import type { Character, DocumentDetail, IdentifiedTurningPoint, NotApplicableTurningPoint, TurningPoint } from '@/api/contracts'
import { createVoice, previewVoice, VOICE_LIMIT_EXCEEDED, VOICE_PROVIDER_UNAVAILABLE } from '@/api/voices'
import { AlertDialog, AlertDialogAction, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { playPcm16 } from './audio-decoder'
import { firstUtteranceLine, spokenCharacterNames } from './fountain-scenes'
import { charactersWithSpokenCues, NARRATOR_SPEAKER, upsertVoiceAssignment, voiceIdForSpeaker, type VoiceAssignment } from './voice-assignments'

export { NARRATOR_SPEAKER }

export type EditableSection = 'character' | 'narrator' | 'turning_point' | 'synopsis' | undefined

const TURNING_POINT_KEYS = ['opportunity', 'changeOfPlans', 'pointOfNoReturn', 'majorSetback', 'climax'] as const
const tpLabel = (translate: (key: string) => string, tp: TurningPoint) => {
  const key = TURNING_POINT_KEYS[tp.tp_number - 1]
  return key ? translate(`turningPoints.${key}`) : tp.label
}

export type AnalysisDraftUpdate = Partial<Pick<DocumentDetail, 'analysis' | 'emotion_arc' | 'metadata' | 'title' | 'narrator' | 'voice_assignments'>>

type VoiceGenerationProgress = { current: number; speaker: string; total: number }

export interface AnalysisContentProps {
  document: DocumentDetail
  isDraft?: boolean
  onDraftChange?: (update: AnalysisDraftUpdate) => void
  titleMode: 'editable' | 'readonly'
}

// Right pane: Characters (including the Narrator as an assignable speaker) plus
// voice generation controls.
export function AnalysisContent({ document, isDraft = false, onDraftChange, titleMode }: AnalysisContentProps) {
  const { t } = useTranslation(['analysis', 'common'])
  const characters = useMemo(() => charactersWithSpokenCues(document), [document])
  const [editor, setEditor] = useState<EditableSection>()
  const [characterIndex, setCharacterIndex] = useState<number>()
  const [charactersDraft, setCharactersDraft] = useState<Character[]>(characters)
  const [narratorDraft, setNarratorDraft] = useState('')
  const [generatingAll, setGeneratingAll] = useState(false)
  const [voiceGenerationProgress, setVoiceGenerationProgress] = useState<VoiceGenerationProgress>()
  const [limitExceeded, setLimitExceeded] = useState(false)
  const [voiceCreationFailed, setVoiceCreationFailed] = useState(false)
  const canEdit = titleMode === 'editable' && Boolean(onDraftChange)
  const narratorTraits = document.narrator?.voice_traits ?? ''
  const assignments = document.voice_assignments ?? []

  const persistAssignments = (next: VoiceAssignment[]) => {
    onDraftChange?.({ voice_assignments: next })
  }

  const openCharacterEditor = (index: number) => {
    setCharactersDraft(characters)
    setCharacterIndex(index)
    setEditor('character')
  }
  const openNarratorEditor = () => {
    setNarratorDraft(narratorTraits)
    setEditor('narrator')
  }
  const apply = () => {
    if (editor === 'narrator') {
      onDraftChange?.({ narrator: { ...document.narrator, voice_traits: narratorDraft } })
    }
    if (editor === 'character' && characterIndex !== undefined) {
      const originalName = characters[characterIndex]?.name
      const editedName = charactersDraft[characterIndex]?.name
      const emotionCharacters = { ...document.emotion_arc.characters }
      if (originalName && editedName && originalName !== editedName && originalName in emotionCharacters) {
        const arc = emotionCharacters[originalName]
        delete emotionCharacters[originalName]
        emotionCharacters[editedName] = arc
      }
      onDraftChange?.({
        analysis: { ...document.analysis, characters: charactersDraft },
        emotion_arc: { ...document.emotion_arc, characters: emotionCharacters },
      })
    }
    setEditor(undefined)
    setCharacterIndex(undefined)
  }

  // Generate voices that are not assigned yet (narrator + characters), keeping
  // each successful result in the unsaved draft before moving to the next one.
  // Stops on a quota-limit error and surfaces the over-limit dialog.
  const generateAllVoices = async () => {
    if (generatingAll) return
    const existingAssignments = document.voice_assignments ?? []
    const spokenNames = new Set(spokenCharacterNames(document.source_fountain))
    const targets: { speaker: string; voice_traits: string }[] = [
      { speaker: NARRATOR_SPEAKER, voice_traits: narratorTraits },
      ...characters.filter((character) => spokenNames.has(character.name)).map((character) => ({ speaker: character.name, voice_traits: character.voice_traits ?? '' })),
    ].filter((target) => target.voice_traits.trim() && !voiceIdForSpeaker(existingAssignments, target.speaker))
    // Nothing to do: every speaker with voice traits already has a voice.
    // Surface this explicitly so the click does not appear to be ignored.
    if (targets.length === 0) {
      toast.info(t('analysis:voices.allAlreadyGenerated'))
      return
    }
    setGeneratingAll(true)
    try {
      let next = document.voice_assignments ?? []
      for (const [index, target] of targets.entries()) {
        setVoiceGenerationProgress({ current: index + 1, speaker: target.speaker, total: targets.length })
        const result = isDraft
          ? await createVoice(document.document_id, { speaker: target.speaker, voice_traits: target.voice_traits }, undefined, true)
          : await createVoice(document.document_id, { speaker: target.speaker, voice_traits: target.voice_traits })
        next = upsertVoiceAssignment(next, result)
        // Keep completed voices in the unsaved draft immediately. If a later
        // speaker fails, the user can retry only that speaker instead of
        // losing the successful assignments from this batch.
        persistAssignments(next)
      }
      toast.success(t('analysis:voices.allGenerated'))
    } catch (error) {
      if (error instanceof ApiClientError && error.code === VOICE_LIMIT_EXCEEDED) {
        setLimitExceeded(true)
      } else if (error instanceof ApiClientError && error.code === VOICE_PROVIDER_UNAVAILABLE) {
        toast.error(t('analysis:voices.providerUnavailable'))
      } else {
        setVoiceCreationFailed(true)
      }
    } finally {
      setGeneratingAll(false)
      setVoiceGenerationProgress(undefined)
    }
  }

  const handleVoiceGenerated = (result: VoiceAssignment) => {
    persistAssignments(upsertVoiceAssignment(document.voice_assignments, result))
  }

  return <div className="flex h-full min-h-0 flex-col">
    <section className="flex min-h-0 flex-1 flex-col rounded-md border bg-background">
      <div className="sticky top-0 z-10 flex shrink-0 items-center justify-between gap-2 border-b bg-background p-4">
        <div>
          <h2 className="text-sm font-semibold">{t('analysis:sections.characters')}</h2>
          {voiceGenerationProgress && <p aria-live="polite" className="mt-1 text-xs text-muted-foreground" role="status">{t('analysis:voices.generationProgress', voiceGenerationProgress)}</p>}
        </div>
        {canEdit && <Button aria-label={t('analysis:voices.generateAll')} disabled={generatingAll} onClick={() => { void generateAllVoices() }} size="sm" variant="outline">{generatingAll ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{generatingAll ? t('analysis:voices.generating') : t('analysis:voices.generateAll')}</Button>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto"><div className="space-y-4 p-4">
        <NarratorSummary canEdit={canEdit} onEdit={openNarratorEditor} voiceTraits={narratorTraits} />
        {characters.map((character, index) => <CharacterSummary canEdit={canEdit} character={character} key={character.name} onEdit={() => openCharacterEditor(index)} />)}
      </div></div>
    </section>
    <AnalysisEditorDialog
      canEdit={canEdit}
      character={editor === 'narrator' ? undefined : characterIndex === undefined ? undefined : charactersDraft[characterIndex]}
      documentId={document.document_id}
      isDraft={isDraft}
      editor={editor}
      narratorTraits={narratorDraft}
      onCharacterChange={(value) => characterIndex !== undefined && setCharactersDraft(charactersDraft.map((character, index) => index === characterIndex ? value : character))}
      onNarratorChange={setNarratorDraft}
      onOpenChange={(open) => { if (!open) { setEditor(undefined); setCharacterIndex(undefined) } }}
      onSave={apply}
      onSynopsisChange={() => undefined}
      onTurningPointChange={() => undefined}
      onVoiceGenerated={handleVoiceGenerated}
      onVoiceCreationFailed={() => setVoiceCreationFailed(true)}
      onVoiceLimitExceeded={() => setLimitExceeded(true)}
      sourceFountain={document.source_fountain}
      synopsis=""
      tp={undefined}
      voiceAssignments={assignments}
    />
    <VoiceLimitDialog onOpenChange={setLimitExceeded} open={limitExceeded} />
    <VoiceCreationFailureDialog onOpenChange={setVoiceCreationFailed} open={voiceCreationFailed} />
  </div>
}

export function AnalysisCollapsible({ children, defaultOpen = true, onEdit, title }: { children: ReactNode; defaultOpen?: boolean; onEdit?: () => void; title: string }) {
  const [open, setOpen] = useState(defaultOpen)
  return <Collapsible className="rounded-md border bg-background" onOpenChange={setOpen} open={open}><CollapsibleTrigger asChild><button className="flex w-full items-center justify-between p-4 text-left hover:bg-accent/40" type="button"><h2 className="text-sm font-semibold">{title}</h2><ChevronDown className={`size-4 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} /></button></CollapsibleTrigger><CollapsibleContent><div className="border-t p-4">{onEdit ? <button className="block w-full text-left" onClick={onEdit} type="button">{children}</button> : children}</div></CollapsibleContent></Collapsible>
}

function AnalysisCharacterItem({ label, value }: { label: string; value?: string | null }) {
  if (!value?.trim()) return null
  return <div><dt className="text-xs font-medium text-muted-foreground">{label}</dt><dd className="mt-1 leading-6 text-muted-foreground">{value}</dd></div>
}

function NotApplicableTurningPointSummary({ tp }: { tp: NotApplicableTurningPoint }) {
  const { t } = useTranslation('analysis')
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-medium">
          TP{tp.tp_number}：{tpLabel(t as unknown as (key: string) => string, tp)}
          <Badge variant="secondary">{t('turningPoints.notApplicable')}</Badge>
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{tp.reason}</p>
    </div>
  )
}

function IdentifiedTurningPointSummary({ canEdit, tp, onEdit }: { canEdit: boolean; tp: IdentifiedTurningPoint; onEdit: () => void }) {
  const { t } = useTranslation('analysis')
  const [open, setOpen] = useState(false)
  const content = (
    <dl className="space-y-2 text-sm">
      <AnalysisCharacterItem label={t('attributes.storyChange')} value={tp.change} />
      {(tp.involved_characters ?? []).map((person) => (
        <div className="rounded-md border p-2" key={`${tp.tp_number}-${person.name}`}>
          <p className="text-sm font-medium">{person.name}</p>
          <AnalysisCharacterItem label={t('attributes.goal')} value={person.goal} />
          <AnalysisCharacterItem label={t('attributes.conflict')} value={person.conflict} />
          <AnalysisCharacterItem label={t('attributes.choice')} value={person.choice} />
          <AnalysisCharacterItem label={t('attributes.action')} value={person.action} />
          <AnalysisCharacterItem label={t('attributes.change')} value={person.change} />
        </div>
      ))}
    </dl>
  )
  return (
    <Collapsible className="rounded-md border bg-background" onOpenChange={setOpen} open={open}>
      <CollapsibleTrigger asChild>
        <button aria-label={t('turningPoints.open', { number: tp.tp_number })} className="flex w-full items-center justify-between p-3 text-left hover:bg-accent/40" type="button">
          <span className="flex items-center gap-2 text-sm font-medium">
            TP{tp.tp_number}：{tpLabel(t as unknown as (key: string) => string, tp)}
            <Badge variant="outline">{t('turningPoints.scene', { count: tp.scene_number })}</Badge>
          </span>
          <ChevronDown className={`size-4 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <button aria-label={t(canEdit ? 'turningPoints.edit' : 'turningPoints.view', { number: tp.tp_number })} className="block w-full border-t p-3 text-left hover:bg-accent/40" onClick={onEdit} type="button">
          {content}
        </button>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function TurningPointSummary({ canEdit, tp, onEdit }: { canEdit: boolean; tp: TurningPoint; onEdit: () => void }) {
  if (tp.availability === 'not_applicable') {
    return <NotApplicableTurningPointSummary tp={tp} />
  }
  return <IdentifiedTurningPointSummary canEdit={canEdit} onEdit={onEdit} tp={tp} />
}

function NarratorSummary({ canEdit, onEdit, voiceTraits }: { canEdit: boolean; onEdit: () => void; voiceTraits: string }) {
  const { t } = useTranslation('analysis')
  const [open, setOpen] = useState(false)
  const label = t('sections.narrator')
  return <Collapsible className="rounded-md border bg-background" onOpenChange={setOpen} open={open}><CollapsibleTrigger asChild><button aria-label={t('a11y.expand', { label })} className="flex w-full items-center justify-between p-3 text-left hover:bg-accent/40" type="button"><span className="flex items-center gap-2 text-sm font-medium">{label}<Badge variant="secondary">{NARRATOR_SPEAKER}</Badge></span><ChevronDown className={`size-4 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} /></button></CollapsibleTrigger><CollapsibleContent><button aria-label={t(canEdit ? 'a11y.edit' : 'a11y.view', { label })} className="block w-full border-t p-3 text-left hover:bg-accent/40" onClick={onEdit} type="button"><dl className="space-y-2 text-sm"><AnalysisCharacterItem label={t('attributes.voiceTraits')} value={voiceTraits || null} /></dl></button></CollapsibleContent></Collapsible>
}

function CharacterSummary({ canEdit, character, onEdit }: { canEdit: boolean; character: Character; onEdit: () => void }) {
  const { t } = useTranslation('analysis')
  const [open, setOpen] = useState(false)
  const related = character.related_turning_points ?? []
  const content = <dl className="space-y-2 text-sm"><AnalysisCharacterItem label={t('attributes.voiceTraits')} value={character.voice_traits} /><AnalysisCharacterItem label={t('attributes.externalGoal')} value={character.external_goal} /><AnalysisCharacterItem label={t('attributes.internalNeed')} value={character.internal_need} /><AnalysisCharacterItem label={t('attributes.fearCost')} value={character.fear_or_cost} /><AnalysisCharacterItem label={t('attributes.obstacle')} value={character.obstacle} /><AnalysisCharacterItem label={t('attributes.choice')} value={character.choice} /><AnalysisCharacterItem label={t('attributes.agency')} value={character.agency} /><AnalysisCharacterItem label={t('attributes.goalOutcome')} value={character.goal_to_outcome} /></dl>
  return <Collapsible className="rounded-md border bg-background" onOpenChange={setOpen} open={open}><CollapsibleTrigger asChild><button aria-label={t('a11y.expand', { label: character.name })} className="flex w-full items-center justify-between p-3 text-left hover:bg-accent/40" type="button"><span className="flex items-center gap-2 text-sm font-medium">{character.name}{related.length > 0 && <Badge variant="outline">TP{related.join('・TP')}</Badge>}</span><ChevronDown className={`size-4 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} /></button></CollapsibleTrigger><CollapsibleContent><button aria-label={t(canEdit ? 'a11y.edit' : 'a11y.view', { label: character.name })} className="block w-full border-t p-3 text-left hover:bg-accent/40" onClick={onEdit} type="button">{content}</button></CollapsibleContent></Collapsible>
}

export interface AnalysisEditorDialogProps {
  canEdit: boolean
  character?: Character
  documentId: string
  isDraft: boolean
  editor: EditableSection
  narratorTraits: string
  onCharacterChange: (value: Character) => void
  onNarratorChange: (value: string) => void
  onOpenChange: (open: boolean) => void
  onSave: () => void
  onSynopsisChange: (value: string) => void
  onTurningPointChange: (value: TurningPoint) => void
  onVoiceGenerated: (result: VoiceAssignment) => void
  onVoiceCreationFailed?: () => void
  onVoiceLimitExceeded: () => void
  sourceFountain: string
  synopsis: string
  tp?: TurningPoint
  voiceAssignments: VoiceAssignment[]
}

export function AnalysisEditorDialog({ canEdit, character, documentId, isDraft, editor, narratorTraits, onCharacterChange, onNarratorChange, onOpenChange, onSave, onSynopsisChange, synopsis, tp, onTurningPointChange, onVoiceGenerated, onVoiceCreationFailed = () => undefined, onVoiceLimitExceeded, sourceFountain, voiceAssignments }: AnalysisEditorDialogProps) {
  const { t } = useTranslation(['analysis', 'common'])
  const [tpTab, setTpTab] = useState(0)
  const updateCharacter = (field: 'voice_traits' | 'external_goal' | 'internal_need' | 'fear_or_cost' | 'obstacle' | 'choice' | 'agency' | 'goal_to_outcome' | 'name', value: string) => character && onCharacterChange({ ...character, [field]: value })
  const toggleTurningPoint = (tpNumber: number) => {
    if (!character) return
    const current = character.related_turning_points ?? []
    const related = current.includes(tpNumber)
      ? current.filter((item) => item !== tpNumber)
      : [...current, tpNumber].sort((a, b) => a - b)
    onCharacterChange({ ...character, related_turning_points: related })
  }
  const updatePerson = (index: number, field: 'name' | 'goal' | 'conflict' | 'choice' | 'action' | 'change', value: string) => {
    if (!tp || tp.availability !== 'identified') return
    onTurningPointChange({ ...tp, involved_characters: tp.involved_characters.map((person, personIndex) => personIndex === index ? { ...person, [field]: value } : person) })
  }
  const removePerson = (index: number) => {
    if (!tp || tp.availability !== 'identified') return
    onTurningPointChange({ ...tp, involved_characters: tp.involved_characters.filter((_, personIndex) => personIndex !== index) })
    setTpTab((current) => Math.max(0, Math.min(current, tp.involved_characters.length - 2)))
  }
  const activePersonIndex = tp && tp.availability === 'identified' ? Math.min(tpTab, tp.involved_characters.length - 1) : 0
  const personTabLabel = (name: string, index: number) => name.trim() || `#${index + 1}`
  return <Dialog onOpenChange={onOpenChange} open={editor !== undefined}><DialogContent className="sm:max-w-xl">{editor === 'synopsis' && <DialogHeader><DialogTitle>{t('analysis:sections.synopsis')}</DialogTitle></DialogHeader>}{editor === 'narrator' && <DialogHeader><DialogTitle>{t('analysis:sections.narrator')}</DialogTitle></DialogHeader>}{editor === 'character' && <DialogTitle className="sr-only">{t('analysis:sections.characters')}</DialogTitle>}{editor === 'turning_point' && tp && <DialogTitle className="sr-only">TP{tp.tp_number}</DialogTitle>}{editor === 'synopsis' && <Textarea aria-label={t('analysis:editor.synopsisAria')} disabled={!canEdit} onChange={(event) => onSynopsisChange(event.target.value)} rows={9} value={synopsis} />}{editor === 'narrator' && <div className="space-y-3"><label className="grid gap-1 text-sm font-medium">{t('analysis:attributes.voiceTraits')}<Textarea aria-label={`${t('analysis:sections.narrator')} ${t('analysis:attributes.voiceTraits')}`} disabled={!canEdit} onChange={(event) => onNarratorChange(event.target.value)} rows={4} value={narratorTraits} /></label><VoiceControls canEdit={canEdit} documentId={documentId} isDraft={isDraft} onVoiceCreationFailed={onVoiceCreationFailed} onVoiceGenerated={onVoiceGenerated} onVoiceLimitExceeded={onVoiceLimitExceeded} sourceFountain={sourceFountain} speaker={NARRATOR_SPEAKER} voiceAssignments={voiceAssignments} voiceTraits={narratorTraits} /></div>}
    {editor === 'turning_point' && tp && tp.availability === 'identified' && <div className="space-y-3"><div className="flex items-center justify-between pr-8"><h3 className="text-sm font-semibold">TP{tp.tp_number}：{tpLabel(t as unknown as (key: string) => string, tp)}</h3><Badge variant="outline">{t('analysis:turningPoints.scene', { count: tp.scene_number })}</Badge></div><label className="grid gap-1 text-sm font-medium">{t('analysis:attributes.storyChange')}<Textarea aria-label={t('analysis:attributes.storyChange')} disabled={!canEdit} onChange={(event) => onTurningPointChange({ ...tp, change: event.target.value })} rows={3} value={tp.change} /></label>{tp.involved_characters.length > 0 && <Tabs onValueChange={(value) => setTpTab(Number(value))} value={String(activePersonIndex)}><TabsList className="w-full justify-start" variant="line">{tp.involved_characters.map((person, index) => <TabsTrigger key={`${tp.tp_number}-${index}`} value={String(index)}>{personTabLabel(person.name, index)}</TabsTrigger>)}</TabsList>{tp.involved_characters.map((person, index) => <TabsContent className="space-y-2" key={`${tp.tp_number}-${index}`} value={String(index)}><label className="grid gap-1 text-sm font-medium">{t('analysis:attributes.name')}<Input aria-label={t('analysis:editor.personName', { count: index + 1 })} disabled={!canEdit} onChange={(event) => updatePerson(index, 'name', event.target.value)} value={person.name} /></label>{(['goal', 'conflict', 'choice', 'action', 'change'] as const).map((field) => <label className="grid gap-1 text-sm font-medium" key={field}>{t(`analysis:attributes.${field}`)}<Textarea aria-label={`${t('analysis:editor.personName', { count: index + 1 })} ${t(`analysis:attributes.${field}`)}`} disabled={!canEdit} onChange={(event) => updatePerson(index, field, event.target.value)} rows={2} value={person[field]} /></label>)}</TabsContent>)}</Tabs>}{canEdit && <div className="flex items-center gap-2"><Button onClick={() => { setTpTab(tp.involved_characters.length); onTurningPointChange({ ...tp, involved_characters: [...tp.involved_characters, { name: '', goal: '', conflict: '', choice: '', action: '', change: '' }] }) }} size="sm" variant="outline"><Plus />{t('analysis:editor.addPerson')}</Button>{tp.involved_characters.length > 0 && <Button aria-label={t('analysis:editor.removePerson', { count: activePersonIndex + 1 })} onClick={() => removePerson(activePersonIndex)} size="sm" variant="outline"><Trash2 />{t('common:actions.delete')}</Button>}</div>}</div>}{editor === 'character' && character && <div className="space-y-3"><label className="grid gap-1 text-sm font-medium">{t('analysis:attributes.name')}<Input aria-label={t('analysis:editor.characterName')} disabled={!canEdit} onChange={(event) => updateCharacter('name', event.target.value)} value={character.name} /></label><label className="grid gap-1 text-sm font-medium">{t('analysis:attributes.voiceTraits')}<Textarea aria-label={`${character.name} ${t('analysis:attributes.voiceTraits')}`} disabled={!canEdit} onChange={(event) => updateCharacter('voice_traits', event.target.value)} rows={2} value={character.voice_traits ?? ''} /></label><VoiceControls canEdit={canEdit} documentId={documentId} isDraft={isDraft} onVoiceCreationFailed={onVoiceCreationFailed} onVoiceGenerated={onVoiceGenerated} onVoiceLimitExceeded={onVoiceLimitExceeded} sourceFountain={sourceFountain} speaker={character.name} voiceAssignments={voiceAssignments} voiceTraits={character.voice_traits ?? ''} />{([['external_goal', 'externalGoal'], ['internal_need', 'internalNeed'], ['fear_or_cost', 'fearCost'], ['obstacle', 'obstacle'], ['choice', 'choice'], ['agency', 'agency'], ['goal_to_outcome', 'goalOutcome']] as const).map(([field, label]) => <CharacterFieldCollapsible defaultOpen={false} fieldLabel={t(`analysis:attributes.${label}`)} key={field}><Textarea aria-label={`${character.name} ${t(`analysis:attributes.${label}`)}`} disabled={!canEdit} onChange={(event) => updateCharacter(field, event.target.value)} rows={2} value={character[field] ?? ''} /></CharacterFieldCollapsible>)}<div className="text-sm font-medium">{t('analysis:attributes.relatedTurningPoints')}<div className="mt-1 flex gap-3">{[1, 2, 3, 4, 5].map((tpNumber) => <label className="flex items-center gap-1 text-xs font-normal" key={tpNumber}><input aria-label={`${character.name} TP${tpNumber}`} checked={(character.related_turning_points ?? []).includes(tpNumber)} className="size-4" disabled={!canEdit} onChange={() => toggleTurningPoint(tpNumber)} type="checkbox" />TP{tpNumber}</label>)}</div></div></div>}<DialogFooter><Button onClick={() => onOpenChange(false)} variant="outline">{canEdit ? t('common:actions.cancel') : t('common:actions.ok')}</Button>{canEdit && <Button onClick={onSave}>{t('common:actions.ok')}</Button>}</DialogFooter></DialogContent></Dialog>
}

// Per-speaker voice controls shown below the Voice traits field: an editable,
// ephemeral check-text field, a Play button (enabled only when a voice exists),
// and a Generate button. Rendered for both characters and the narrator.
function VoiceControls({ canEdit, documentId, isDraft, onVoiceCreationFailed, onVoiceGenerated, onVoiceLimitExceeded, sourceFountain, speaker, voiceAssignments, voiceTraits }: {
  canEdit: boolean
  documentId: string
  isDraft: boolean
  onVoiceCreationFailed: () => void
  onVoiceGenerated: (result: VoiceAssignment) => void
  onVoiceLimitExceeded: () => void
  sourceFountain: string
  speaker: string
  voiceAssignments: VoiceAssignment[]
  voiceTraits: string
}) {
  const { t } = useTranslation(['analysis', 'common'])
  const [generating, setGenerating] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [checkText, setCheckText] = useState(() => firstUtteranceLine(sourceFountain, speaker, NARRATOR_SPEAKER))
  const voiceId = voiceIdForSpeaker(voiceAssignments, speaker)

  const generate = async () => {
    if (generating || !voiceTraits.trim()) return
    setGenerating(true)
    try {
      const result = isDraft
        ? await createVoice(documentId, { speaker, voice_traits: voiceTraits }, undefined, true)
        : await createVoice(documentId, { speaker, voice_traits: voiceTraits })
      onVoiceGenerated(result)
    } catch (error) {
      if (error instanceof ApiClientError && error.code === VOICE_LIMIT_EXCEEDED) {
        onVoiceLimitExceeded()
      } else if (error instanceof ApiClientError && error.code === VOICE_PROVIDER_UNAVAILABLE) {
        toast.error(t('analysis:voices.providerUnavailable'))
      } else {
        onVoiceCreationFailed()
      }
    } finally {
      setGenerating(false)
    }
  }

  const play = async () => {
    if (playing || !voiceId || !checkText) return
    setPlaying(true)
    try {
      const pcm = isDraft
        ? await previewVoice(documentId, { voice_id: voiceId, text: checkText }, fetch, true)
        : await previewVoice(documentId, { voice_id: voiceId, text: checkText })
      await playPcm16(pcm)
    } finally {
      setPlaying(false)
    }
  }

  return <div className="space-y-2 rounded-md border bg-muted/30 p-2">
    <div>
      <label className="grid gap-1 text-xs font-medium text-muted-foreground">{t('analysis:voices.checkText')}
        <Textarea aria-label={`${speaker} ${t('analysis:voices.checkText')}`} className="min-h-16 bg-background text-sm leading-6 text-foreground" onChange={(event) => setCheckText(event.target.value)} rows={2} value={checkText} />
      </label>
    </div>
    {generating && <p aria-live="polite" className="text-xs text-muted-foreground" role="status">{t('analysis:voices.generatingSpeaker', { speaker })}</p>}
    <div className="flex items-center gap-2">
      {canEdit && <Button aria-label={`${speaker} ${t('analysis:voices.generate')}`} disabled={generating || !voiceTraits.trim()} onClick={() => { void generate() }} size="sm" variant="default">{generating ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{generating ? t('analysis:voices.generating') : t('analysis:voices.generate')}</Button>}
      <Button aria-label={`${speaker} ${t('analysis:voices.play')}`} disabled={!voiceId || !checkText || playing} onClick={() => { void play() }} size="sm" variant="outline"><Play />{t('analysis:voices.play')}</Button>
    </div>
  </div>
}

function VoiceLimitDialog({ onOpenChange, open }: { onOpenChange: (open: boolean) => void; open: boolean }) {
  const { t } = useTranslation(['analysis', 'common'])
  return <AlertDialog onOpenChange={onOpenChange} open={open}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:sections.characters')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:voices.limitExceeded')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogAction onClick={() => onOpenChange(false)}>{t('common:actions.close')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function VoiceCreationFailureDialog({ onOpenChange, open }: { onOpenChange: (open: boolean) => void; open: boolean }) {
  const { t } = useTranslation(['analysis', 'common'])
  return <AlertDialog onOpenChange={onOpenChange} open={open}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:voices.creationFailedTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:voices.creationFailed')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogAction onClick={() => onOpenChange(false)}>{t('common:actions.close')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function CharacterFieldCollapsible({ children, defaultOpen = true, fieldLabel }: { children: ReactNode; defaultOpen?: boolean; fieldLabel: string }) {
  const [open, setOpen] = useState(defaultOpen)
  return <Collapsible className="rounded-md border bg-background" onOpenChange={setOpen} open={open}><CollapsibleTrigger asChild><button className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium hover:bg-accent/40" type="button">{fieldLabel}<ChevronDown className={`size-4 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} /></button></CollapsibleTrigger><CollapsibleContent><div className="border-t p-2">{children}</div></CollapsibleContent></Collapsible>
}
