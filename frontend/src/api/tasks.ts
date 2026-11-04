import { z } from 'zod'

import {
  TASK_SSE_INITIAL_RETRY_DELAY_MS,
  TASK_SSE_MAX_RETRIES,
  TASK_SSE_MAX_RETRY_DELAY_MS,
} from '@/config/narravant'

import { ApiClientError, apiRequest } from './client'
import { CancelTaskApiV1TasksTaskIdCancelPostResponse } from './generated/contracts'
import {
  taskCancelledEventSchema,
  taskCompletedEventSchema,
  taskErrorEventSchema,
  taskProgressEventSchema,
} from './generated/task-events'
import { consumeSseStream, SseTransportError } from './sse'

export type TaskProgress = z.infer<typeof taskProgressEventSchema>
export type TaskStreamCallbacks = {
  onProgress?: (progress: TaskProgress) => void
  onCompleted?: (event: z.infer<typeof taskCompletedEventSchema>) => void
  onError?: (event: z.infer<typeof taskErrorEventSchema>) => void
  onCancelled?: (event: z.infer<typeof taskCancelledEventSchema>) => void
}

const NON_RETRYABLE_STATUSES = new Set([403, 404, 422])

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout)
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'))
    }
    const timeout = window.setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve() }, milliseconds)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export interface TaskProgressCursor {
  lastEventId?: string
  deliveredEventIds: Set<string>
}

export async function streamTaskProgress(
  taskId: string,
  callbacks: TaskStreamCallbacks,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
  cursor: TaskProgressCursor = { deliveredEventIds: new Set() },
): Promise<void> {
  let retryCount = 0
  while (!signal?.aborted) {
    let terminal = false
    let delivered = false
    try {
      let response: Response
      try {
        response = await fetcher(`/api/v1/tasks/${taskId}/progress`, {
          headers: { Accept: 'text/event-stream', ...(cursor.lastEventId ? { 'Last-Event-ID': cursor.lastEventId } : {}) },
          signal,
        })
      } catch (error) {
        if (signal?.aborted) return
        throw new SseTransportError(error instanceof Error ? error.message : 'Connection failed.')
      }
      if (!response.ok) {
        if (NON_RETRYABLE_STATUSES.has(response.status) || response.status === 401) {
          throw new ApiClientError(response.status, 'SSE_CONNECTION_FAILED', 'Failed to connect to the progress stream.')
        }
        throw new SseTransportError('The progress stream is unavailable.')
      }
      await consumeSseStream(response, (event) => {
        if (terminal || (event.id && cursor.deliveredEventIds.has(event.id))) return
        let isTerminal = false
        if (event.event === 'progress') { const data = taskProgressEventSchema.parse(event.data); callbacks.onProgress?.(data) }
        else if (event.event === 'completed') {
          const data = taskCompletedEventSchema.parse(event.data); callbacks.onCompleted?.(data); isTerminal = true
        } else if (event.event === 'error') {
          const data = taskErrorEventSchema.parse(event.data); callbacks.onError?.(data); isTerminal = true
        } else if (event.event === 'cancelled') {
          const data = taskCancelledEventSchema.parse(event.data); callbacks.onCancelled?.(data); isTerminal = true
        } else return
        if (event.id) {
          cursor.lastEventId = event.id
          cursor.deliveredEventIds.add(event.id)
        }
        delivered = true
        terminal = isTerminal
      })
    } catch (error) {
      if (terminal || signal?.aborted) return
      if (!(error instanceof SseTransportError)) throw error
    }
    if (terminal || signal?.aborted) return
    retryCount = delivered ? 0 : retryCount + 1
    if (retryCount >= TASK_SSE_MAX_RETRIES) {
      throw new ApiClientError(200, 'SSE_DISCONNECTED', 'The progress stream disconnected before completion.')
    }
    const delay = Math.min(TASK_SSE_INITIAL_RETRY_DELAY_MS * 2 ** retryCount, TASK_SSE_MAX_RETRY_DELAY_MS)
    try { await abortableDelay(delay, signal) } catch (error) {
      if (signal?.aborted) return
      throw error
    }
  }
}

export function cancelTask(taskId: string, fetcher?: typeof fetch) {
  return apiRequest(`/api/v1/tasks/${taskId}/cancel`, CancelTaskApiV1TasksTaskIdCancelPostResponse, {
    method: 'POST',
    fetcher,
  })
}