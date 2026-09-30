import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

export interface ValenceSimilarityRow {
  label: string
  value: string
}

export function ValenceSimilarityDialog({ items, onOpenChange, open }: { items: ValenceSimilarityRow[]; onOpenChange: (open: boolean) => void; open: boolean }) {
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent className="sm:max-w-sm"><DialogHeader><DialogTitle>Valence Similarity(%)</DialogTitle></DialogHeader><div className="space-y-2 py-2">{items.length === 0 ? <p className="text-sm text-muted-foreground">No Valence data is available.</p> : items.map((item) => <div className="flex items-center gap-3" key={item.label}><span className="flex-1 text-sm font-medium">{item.label}</span><Input aria-label={`${item.label} similarity`} className="h-8 w-24 text-right font-mono" readOnly value={item.value} /></div>)}</div><DialogFooter><Button onClick={() => onOpenChange(false)}>OK</Button></DialogFooter></DialogContent></Dialog>
}
