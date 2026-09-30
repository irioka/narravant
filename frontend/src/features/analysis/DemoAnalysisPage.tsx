import { Bot, ChevronDown, Download, FileClock, FileText, History, Save, Send, Share2, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { Link } from 'react-router-dom'

import type { DocumentDetail } from '@/api/contracts'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { AnalysisContent } from './AnalysisContent'
import { ValenceSimilarityDialog } from './ValenceSimilarityDialog'
import { analysisDemoDocument } from './demo-data'

const previousDocument: DocumentDetail = { ...analysisDemoDocument, version_id: 6, current_version_id: 6, updated_at: '2026-09-08T09:00:00+09:00', metadata: { synopsis: '柚樹は告白したあと、主人との距離を測りかねている。' }, source_fountain: 'Title: コンフェッションズ short\n\n# あらすじ\n柚樹は告白したあと、主人との距離を測りかねている。\n\nINT. 生田高校・視聴覚室 - 放課後\n\n二人の間に沈黙が落ちる。', emotion_arc: { ...analysisDemoDocument.emotion_arc, valence: [5, 3, 4] } }
const demoVersions = [analysisDemoDocument, previousDocument]
const similarityRows = [{ label: 'rise_fall_rise', value: '59.97' }, { label: 'fall_rise', value: '56.23' }, { label: 'rise', value: '19.44' }, { label: 'fall', value: '13.96' }, { label: 'fall_rise_fall', value: '-29.55' }, { label: 'rise_fall', value: '-42.48' }]

export function DemoAnalysisPage() {
  const [document, setDocument] = useState(analysisDemoDocument)
  const [similarityOpen, setSimilarityOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  return <main className="flex h-svh min-w-[1180px] flex-col overflow-hidden bg-background"><header className="relative flex h-10 shrink-0 items-center border-b bg-card px-3"><Button aria-label="Back to documents" asChild size="icon" variant="ghost"><Link to="/documents/demo"><FileText className="size-4 text-primary" /></Link></Button><span className="absolute left-1/2 -translate-x-1/2 text-sm font-semibold">NARRAVANT</span><div className="ml-auto flex items-center gap-1"><Button aria-label="Upload document" onClick={() => window.document.getElementById('demo-editor-upload')?.click()} size="icon" variant="ghost"><Upload /></Button><Button aria-label="Download document" onClick={() => window.dispatchEvent(new Event('narravant-demo-download'))} size="icon" variant="ghost"><Download /></Button><Button aria-label="Share" onClick={() => setShareOpen(true)} size="icon" variant="ghost"><Share2 /></Button><Button aria-label="Delete" onClick={() => setDeleteOpen(true)} size="icon" variant="ghost"><Trash2 className="text-destructive" /></Button><DropdownMenu><DropdownMenuTrigger asChild><Button className="gap-1 text-xs" variant="ghost">dev-user@narravant.local<ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuLabel>Account</DropdownMenuLabel><DropdownMenuSeparator /><DropdownMenuItem>Log out</DropdownMenuItem></DropdownMenuContent></DropdownMenu></div></header><Group className="flex-1" orientation="horizontal"><Panel defaultSize="30" minSize="24"><aside className="h-full overflow-hidden bg-card"><ScrollArea className="h-full"><AnalysisContent key={document.version_id} document={document} onDraftChange={(update) => setDocument((current) => ({ ...current, ...update, metadata: update.metadata ?? current.metadata, analysis: update.analysis ?? current.analysis, emotion_arc: update.emotion_arc ?? current.emotion_arc }))} titleMode="editable" /></ScrollArea></aside></Panel><Separator className="w-px bg-border" /><Panel defaultSize="47" minSize="30"><DemoEditorPane key={document.version_id} document={document} /></Panel><Separator className="w-px bg-border" /><Panel defaultSize="23" minSize="18"><DemoAssistantPane document={document} onSelectVersion={setDocument} /></Panel></Group><ValenceSimilarityDialog items={similarityRows} onOpenChange={setSimilarityOpen} open={similarityOpen} /><DemoShareDialog onOpenChange={setShareOpen} open={shareOpen} /><AlertDialog onOpenChange={setDeleteOpen} open={deleteOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Delete document?</AlertDialogTitle><AlertDialogDescription>Delete “{document.title}”? This action cannot be undone.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog></main>
}

function DemoAssistantPane({ document, onSelectVersion }: { document: DocumentDetail; onSelectVersion: (document: DocumentDetail) => void }) {
  const formatVersionDate = (value: string) => new Intl.DateTimeFormat('ja-JP', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
  return <section className="flex h-full flex-col bg-card"><Tabs className="flex h-full flex-col" defaultValue="assistant"><div className="flex h-10 items-center border-b px-2"><TabsList className="h-7"><TabsTrigger value="assistant"><Bot />Assistant</TabsTrigger><TabsTrigger value="history"><History />History</TabsTrigger></TabsList></div><TabsContent className="mt-0 flex min-h-0 flex-1 flex-col" value="assistant"><ScrollArea className="flex-1"><p className="m-3 rounded-lg bg-muted p-3 text-sm leading-6">主人が返答をためらう場面で、二人の視線や間を増やすと感情の変化が明確になります。</p></ScrollArea><div className="flex gap-2 border-t p-3"><Input placeholder="Ask about this story" /><Button aria-label="Send" size="icon"><Send /></Button></div></TabsContent><TabsContent className="mt-0 flex-1" value="history"><ScrollArea className="h-full"><div className="p-2">{demoVersions.map((version) => <Button className="mb-1 w-full justify-start gap-2" key={version.version_id} onClick={() => onSelectVersion(version)} variant={document.version_id === version.version_id ? 'secondary' : 'ghost'}><FileClock className="size-4" />v{version.version_id} · {formatVersionDate(version.updated_at)}</Button>)}</div></ScrollArea></TabsContent></Tabs></section>
}

function DemoEditorPane({ document }: { document: DocumentDetail }) {
  const [text, setText] = useState(document.source_fountain ?? '')
  const download = useCallback(() => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const link = window.document.createElement('a')
    link.href = url
    link.download = `${document.title}.fountain`
    link.click()
    URL.revokeObjectURL(url)
  }, [document.title, text])
  useEffect(() => {
    window.addEventListener('narravant-demo-download', download)
    return () => window.removeEventListener('narravant-demo-download', download)
  }, [download])
  return <section className="flex h-full flex-col"><div className="flex h-10 items-center border-b bg-card px-3"><span className="text-sm font-medium">本文エディタ</span><input accept=".fountain,.txt,text/plain" className="hidden" id="demo-editor-upload" onChange={async (event) => { const file = event.target.files?.[0]; if (file) setText(await file.text()); event.target.value = '' }} type="file" /><Button className="ml-auto" size="sm" variant="outline"><Save />Save</Button></div><Textarea aria-label="脚本本文" className="min-h-0 flex-1 resize-none rounded-none border-0 p-7 font-mono text-sm leading-7 shadow-none" onChange={(event) => setText(event.target.value)} value={text} /></section>
}

function DemoShareDialog({ onOpenChange, open }: { onOpenChange: (open: boolean) => void; open: boolean }) {
  const [email, setEmail] = useState('')
  const [users, setUsers] = useState(['writer@example.com', 'reviewer@example.com'])
  const addUser = () => {
    const user = email.trim().toLowerCase()
    if (!/^\S+@\S+\.\S+$/.test(user) || users.includes(user)) return
    setUsers((current) => [...current, user])
    setEmail('')
  }
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent><DialogHeader><DialogTitle>Share settings</DialogTitle></DialogHeader><div className="space-y-3"><div className="flex gap-2"><Input aria-label="Email address to share with" onChange={(event) => setEmail(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addUser() } }} placeholder="user@example.com" value={email} /><Button onClick={addUser} type="button">Add</Button></div>{users.length === 0 ? <div className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">No shared users</div> : <div className="divide-y rounded-md border">{users.map((user) => <div className="flex items-center px-3 py-2 text-sm" key={user}><span>{user}</span><Button className="ml-auto" onClick={() => setUsers((current) => current.filter((item) => item !== user))} size="sm" variant="ghost">Remove</Button></div>)}</div>}</div><DialogFooter><Button onClick={() => onOpenChange(false)} variant="outline">Cancel</Button><Button onClick={() => onOpenChange(false)}>Save</Button></DialogFooter></DialogContent></Dialog>
}
