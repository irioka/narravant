import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import type { DocumentItem } from '@/api/contracts'
import { getDocument, listDocuments, updateDocument } from '@/api/documents'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import type { DocumentSortField, DocumentSortTerm } from '@/config/narravant'
import { DOCUMENT_PAGE_SIZE } from '@/config/narravant'

const COLUMN_WIDTHS_KEY = 'narravant.documentList.columnWidths'
const DEFAULT_COLUMN_WIDTHS = { version: 88, savedAt: 144 }
const MIN_COLUMN_WIDTHS = { version: 64, savedAt: 96 }
const MAX_COLUMN_WIDTH = 480
const MIN_TABLE_WIDTH = `calc(${DEFAULT_COLUMN_WIDTHS.version + DEFAULT_COLUMN_WIDTHS.savedAt}px + 12rem + 2.5rem)`

type ResizableColumn = keyof typeof DEFAULT_COLUMN_WIDTHS
type ColumnWidths = typeof DEFAULT_COLUMN_WIDTHS

function clampColumnWidth(column: ResizableColumn, value: number): number {
  return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTHS[column], value))
}

function readColumnWidths(): ColumnWidths {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(COLUMN_WIDTHS_KEY) ?? 'null')
    if (!value || typeof value !== 'object') return DEFAULT_COLUMN_WIDTHS
    const stored = value as Record<string, unknown>
    return {
      version: typeof stored.version === 'number' && Number.isFinite(stored.version)
        ? clampColumnWidth('version', stored.version)
        : DEFAULT_COLUMN_WIDTHS.version,
      savedAt: typeof stored.savedAt === 'number' && Number.isFinite(stored.savedAt)
        ? clampColumnWidth('savedAt', stored.savedAt)
        : DEFAULT_COLUMN_WIDTHS.savedAt,
    }
  } catch {
    return DEFAULT_COLUMN_WIDTHS
  }
}

function ResizeHandle({
  column,
  label,
  value,
  onResize,
}: {
  column: ResizableColumn
  label: string
  value: number
  onResize: (column: ResizableColumn, value: number) => void
}) {
  const dragRef = useRef<{ startX: number; startWidth: number; pointerId: number } | undefined>(undefined)
  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return
    dragRef.current = undefined
  }

  return <div
    aria-label={label}
    aria-orientation="vertical"
    aria-valuemax={MAX_COLUMN_WIDTH}
    aria-valuemin={MIN_COLUMN_WIDTHS[column]}
    aria-valuenow={value}
    className="absolute inset-y-[-0.375rem] left-0 z-10 w-2 touch-none cursor-col-resize"
    onKeyDown={(event) => {
      const delta = event.key === 'ArrowRight' ? -8 : event.key === 'ArrowLeft' ? 8 : 0
      if (delta) {
        event.preventDefault()
        onResize(column, value + delta)
      }
    }}
    onPointerCancel={endDrag}
    onPointerDown={(event) => {
      event.preventDefault()
      event.stopPropagation()
      dragRef.current = { startX: event.clientX, startWidth: value, pointerId: event.pointerId }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }}
    onPointerMove={(event) => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      onResize(column, drag.startWidth + drag.startX - event.clientX)
    }}
    onPointerUp={endDrag}
    role="separator"
    tabIndex={0}
  />
}

function formatDate(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('ja-JP', { day: '2-digit', hour: '2-digit', minute: '2-digit', month: '2-digit', year: 'numeric' }).format(parsed)
}

function toggleSort(current: DocumentSortTerm | undefined, field: DocumentSortField): DocumentSortTerm {
  if (current?.field === field) return { field, direction: current.direction === 'asc' ? 'desc' : 'asc' }
  return { field, direction: 'asc' }
}

export function DocumentList({ activeDocumentId, onBeforeNavigate }: { activeDocumentId?: string; onBeforeNavigate?: () => void }) {
  const { t } = useTranslation('documents')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchDraft, setSearchDraft] = useState('')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<DocumentSortTerm>()
  const [editingId, setEditingId] = useState<string>()
  const [titleDraft, setTitleDraft] = useState('')
  const [columnWidths, setColumnWidths] = useState<ColumnWidths>(readColumnWidths)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const sorts = useMemo(() => (sort ? [sort] : undefined), [sort])
  const documentsQuery = useInfiniteQuery({
    queryKey: ['documents', query, sort?.field, sort?.direction],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listDocuments({ query, sorts, limit: DOCUMENT_PAGE_SIZE, offset: pageParam }),
    getNextPageParam: (page) => page.offset + page.items.length < page.total ? page.offset + page.items.length : undefined,
  })
  const documents = useMemo(() => documentsQuery.data?.pages.flatMap((page) => page.items) ?? [], [documentsQuery.data])
  const { fetchNextPage, hasNextPage, isFetchingNextPage } = documentsQuery
  const gridTemplateColumns = `minmax(0, 1fr) ${columnWidths.version}px ${columnWidths.savedAt}px`

  useEffect(() => {
    try {
      window.localStorage.setItem(COLUMN_WIDTHS_KEY, JSON.stringify(columnWidths))
    } catch {
      // A disabled or full localStorage must not make the list unusable.
    }
  }, [columnWidths])

  const resizeColumn = (column: ResizableColumn, value: number) => {
    setColumnWidths((current) => ({ ...current, [column]: clampColumnWidth(column, value) }))
  }

  const renameMutation = useMutation({
    mutationFn: async ({ document, title }: { document: DocumentItem; title: string }) => {
      // The list can remain mounted while a Save or voice assignment advances
      // the optimistic-lock counter. Fetch the current document state
      // state immediately before renaming instead of submitting a stale value.
      const latest = await getDocument(document.document_id)
      return updateDocument(document.document_id, { expected_version: latest.expected_version, title })
    },
    onSuccess: async () => {
      setEditingId(undefined)
      await queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: () => toast.error(t('states.renameFailed')),
  })

  const startRename = (document: DocumentItem) => { onBeforeNavigate?.(); setEditingId(document.document_id); setTitleDraft(document.title) }
  const commitRename = (document: DocumentItem) => {
    onBeforeNavigate?.()
    const title = titleDraft.trim()
    if (!title || title === document.title) { setEditingId(undefined); return }
    renameMutation.mutate({ document, title })
  }

  useEffect(() => {
    const target = sentinelRef.current
    const root = scrollRef.current
    if (!target || !root || !hasNextPage || isFetchingNextPage) return
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) void fetchNextPage() }, { root, rootMargin: '200px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [documents.length, fetchNextPage, hasNextPage, isFetchingNextPage])

  const sortHeader = (field: DocumentSortField, label: string, className?: string) => (
    <button aria-label={t('list.sortBy', { column: label })} className={`flex items-center gap-0.5 hover:text-foreground ${className ?? ''}`} onClick={() => setSort((current) => toggleSort(current, field))} type="button">
      {label}{sort?.field === field && (sort.direction === 'asc' ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />)}
    </button>
  )

  return <div className="flex h-full min-h-0 flex-col">
    <div className="shrink-0 p-2"><div className="relative"><Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label={t('search.ariaLabel')} className="h-8 bg-card pl-8 text-xs" onChange={(event) => setSearchDraft(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && setQuery(searchDraft.trim())} placeholder={t('search.placeholder')} value={searchDraft} /></div></div>
    <ScrollArea className="min-h-0 flex-1"><div ref={scrollRef} className="overflow-auto"><div style={{ minWidth: `max(100%, ${MIN_TABLE_WIDTH})` }}><div className="grid items-center gap-2 border-b px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground" style={{ gridTemplateColumns }}><div className="min-w-0">{sortHeader('title', t('table.title'), 'min-w-0')}</div><div className="relative min-w-0">{sortHeader('version_id', t('table.version'), 'min-w-0 justify-self-start whitespace-nowrap')}<ResizeHandle column="version" label={t('list.resizeColumn', { column: t('table.version') })} onResize={resizeColumn} value={columnWidths.version} /></div><div className="relative min-w-0">{sortHeader('updated_at', t('table.savedAt'), 'min-w-0 justify-self-start whitespace-nowrap')}<ResizeHandle column="savedAt" label={t('list.resizeColumn', { column: t('table.savedAt') })} onResize={resizeColumn} value={columnWidths.savedAt} /></div></div>
      {documentsQuery.isPending && Array.from({ length: 6 }, (_, index) => <div className="px-3 py-2" key={index}><Skeleton className="h-4 w-full" /></div>)}
      {documentsQuery.isError && <p className="px-3 py-6 text-center text-xs text-muted-foreground">{t('states.loadFailed')}</p>}
      {!documentsQuery.isPending && !documentsQuery.isError && documents.length === 0 && <p className="px-3 py-6 text-center text-xs text-muted-foreground">{t('states.noMatches')}</p>}
      {documents.map((document) => editingId === document.document_id
        ? <div className="grid w-full items-center gap-2 border-b px-3 py-1.5" key={document.document_id} style={{ gridTemplateColumns }}><Input aria-label={t('list.titleInputAria')} autoFocus className="h-6 min-w-0 text-xs" disabled={renameMutation.isPending} onBlur={() => commitRename(document)} onChange={(event) => setTitleDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') commitRename(document); if (event.key === 'Escape') setEditingId(undefined) }} value={titleDraft} /><span /><span /></div>
        : <button className={`grid w-full items-center gap-2 border-b px-3 py-2 text-left text-xs hover:bg-accent/50 ${activeDocumentId === document.document_id ? 'bg-accent/70' : ''}`} key={document.document_id} onClick={() => { onBeforeNavigate?.(); navigate(`/analysis/${document.document_id}`) }} onDoubleClick={(event) => { event.preventDefault(); startRename(document) }} style={{ gridTemplateColumns }} type="button"><span className="min-w-0 truncate font-medium">{document.title}</span><span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground">v{document.version_id}</span><span className="min-w-0 truncate font-mono text-[10px] tabular-nums text-muted-foreground">{document.is_saved ? formatDate(document.updated_at) : '----/--/-- --:--'}</span></button>)}
      <div ref={sentinelRef} />{isFetchingNextPage && <p className="px-3 py-2 text-center text-xs text-muted-foreground">{t('states.loadingMore')}</p>}
    </div></div></ScrollArea>
  </div>
}
