import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TaskProgressDialog, type TaskProgressState } from './TaskProgressDialog'

const activeImport: TaskProgressState = {
  taskId: 'task-1',
  operation: 'import',
  phase: 'analyzing',
  percentage: 42,
  startedAt: new Date('2026-09-16T00:00:00Z').getTime(),
  receivedCharacters: 12_345,
}

function renderDialog(task: TaskProgressState | undefined = activeImport) {
  const onCancel = vi.fn()
  const onDismiss = vi.fn()
  const view = render(
    <TaskProgressDialog
      now={new Date('2026-09-16T00:01:05Z').getTime()}
      onCancel={onCancel}
      onDismiss={onDismiss}
      task={task}
    />,
  )
  return { ...view, onCancel, onDismiss }
}

describe('TaskProgressDialog', () => {
  it('renders Import and reanalysis with one English progress row and no remaining time', () => {
    renderDialog()
    expect(screen.getByRole('heading', { name: 'Importing and analyzing' })).toBeInTheDocument()
    const phase = screen.getByText('Analyzing structure and emotional arc…')
    expect(within(phase.parentElement!).getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('Start time')).toBeInTheDocument()
    expect(screen.getByText('Elapsed time')).toBeInTheDocument()
    expect(screen.getByText('1:05')).toBeInTheDocument()
    expect(screen.queryByText(/Remaining|残り|計算中/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Gemini received characters')).toHaveTextContent(/^12,345$/)
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()

    cleanup()
    renderDialog({ ...activeImport, operation: 'reanalyze', phase: 'queued' })
    expect(screen.getByRole('heading', { name: 'Reanalyzing Emotional Arc' })).toBeInTheDocument()
    expect(screen.getByText('Queued')).toBeInTheDocument()
  })

  it('requests cancellation once and disables the English button while cancelling', async () => {
    const user = userEvent.setup()
    const { onCancel } = renderDialog()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)

    cleanup()
    renderDialog({ ...activeImport, cancelRequestPending: true })
    expect(screen.getByRole('button', { name: 'Cancelling…' })).toBeDisabled()

    cleanup()
    renderDialog({ ...activeImport, phase: 'cancelling', cancelRequestPending: false })
    expect(screen.getByRole('button', { name: 'Cancelling…' })).toBeDisabled()
  })

  it('renders completed and cancelled as dismissible states and hides error progress', async () => {
    const user = userEvent.setup()
    const completed = renderDialog({
      ...activeImport,
      phase: 'completed',
      percentage: 100,
      terminal: 'completed',
    })
    expect(screen.getByText('Completed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(completed.onDismiss).toHaveBeenCalledTimes(1)

    cleanup()
    const cancelled = renderDialog({
      ...activeImport,
      phase: 'cancelled',
      terminal: 'cancelled',
    })
    expect(screen.getByText('Cancelled')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(cancelled.onDismiss).toHaveBeenCalledTimes(1)

    cleanup()
    renderDialog({ ...activeImport, terminal: 'error' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
