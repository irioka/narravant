import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { progressPhaseLabel, type DisplayProgressPhase } from './progress-phase'

export type TaskOperation = 'import' | 'reanalyze'
export type TaskTerminalState = 'completed' | 'error' | 'cancelled'

export interface TaskProgressState {
  taskId?: string
  operation: TaskOperation
  phase: DisplayProgressPhase
  percentage: number
  startedAt: number
  receivedCharacters?: number | null
  cancelRequestPending?: boolean
  terminal?: TaskTerminalState
}

export interface TaskProgressDialogProps {
  now: number
  onCancel: () => void
  onDismiss: () => void
  task?: TaskProgressState
}

const START_TIME_FORMAT = new Intl.DateTimeFormat('en-US', { timeStyle: 'medium' })

function formatElapsedTime(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function TaskProgressDialog({ now, onCancel, onDismiss, task }: TaskProgressDialogProps) {
  if (!task || task.terminal === 'error') return null

  const elapsedSeconds = Math.max(0, Math.floor((now - task.startedAt) / 1000))
  const isActive = Boolean(task.taskId && !task.terminal)
  const isCancelling = task.cancelRequestPending === true || task.phase === 'cancelling'
  const title = task.operation === 'import' ? 'Importing and analyzing' : 'Reanalyzing Emotional Arc'

  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open && task.terminal) onDismiss()
      }}
      open
    >
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">
            Progress for the current {task.operation === 'import' ? 'import' : 'Emotional Arc reanalysis'} task.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 py-3">
          <div className="flex items-center justify-between gap-4 text-sm">
            <span>{progressPhaseLabel(task.phase)}</span>
            <span className="shrink-0 tabular-nums">{task.percentage}%</span>
          </div>
          <Progress aria-label="Task progress" value={task.percentage} />
          <div className="grid grid-cols-[1fr_auto] items-end gap-4 text-xs text-muted-foreground">
            <dl className="grid grid-cols-2 gap-4">
              <div>
                <dt>Start time</dt>
                <dd>{START_TIME_FORMAT.format(new Date(task.startedAt))}</dd>
              </div>
              <div>
                <dt>Elapsed time</dt>
                <dd className="tabular-nums">{formatElapsedTime(elapsedSeconds)}</dd>
              </div>
            </dl>
            {task.receivedCharacters != null && (
              <output
                aria-label="Gemini received characters"
                className="text-right tabular-nums"
              >
                {task.receivedCharacters.toLocaleString('en-US')}
              </output>
            )}
          </div>
        </div>
        {(isActive || task.terminal) && (
          <DialogFooter>
            {isActive ? (
              <Button disabled={isCancelling} onClick={onCancel} variant="outline">
                {isCancelling ? 'Cancelling…' : 'Cancel'}
              </Button>
            ) : (
              <Button onClick={onDismiss}>Close</Button>
            )}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
