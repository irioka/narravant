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
import { consumeSseStream } from './sse'

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
    const timeout = window.setTimeout(resolve, milliseconds)
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timeout)
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

export async function streamTaskProgress(
  taskId: string,
  callbacks: TaskStreamCallbacks,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<void> {
  let lastEventId: string | undefined
  let retryCount = 0
  const deliveredEventIds = new Set<string>()

  while (!signal?.aborted) {
    const response = await fetcher(`/api/v1/tasks/${taskId}/progress`, {
      headers: {
        Accept: 'text/event-stream',
        ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
      },
      signal,
    })
    if (!response.ok) {
      if (NON_RETRYABLE_STATUSES.has(response.status) || response.status === 401) {
        throw new ApiClientError(response.status, 'SSE_CONNECTION_FAILED', 'Failed to connect to the progress stream.')
      }
    }

    let terminal = false
    const { receivedBytes } = await consumeSseStream(response, (event) => {
      if (event.id) {
        lastEventId = event.id
        if (deliveredEventIds.has(event.id)) return
        deliveredEventIds.add(event.id)
      }
      if (event.event === 'progress') callbacks.onProgress?.(taskProgressEventSchema.parse(event.data))
      if (event.event === 'completed') {
        terminal = true
        callbacks.onCompleted?.(taskCompletedEventSchema.parse(event.data))
      }
      if (event.event === 'error') {
        terminal = true
        callbacks.onError?.(taskErrorEventSchema.parse(event.data))
      }
      if (event.event === 'cancelled') {
        terminal = true
        callbacks.onCancelled?.(taskCancelledEventSchema.parse(event.data))
      }
    })
    if (terminal || signal?.aborted) return

    if (receivedBytes > 0) {
      retryCount = 0
    } else {
      retryCount += 1
    }

    if (retryCount >= TASK_SSE_MAX_RETRIES) {
      throw new ApiClientError(200, 'SSE_DISCONNECTED', 'The progress stream disconnected before completion.')
    }
    const delay = Math.min(TASK_SSE_INITIAL_RETRY_DELAY_MS * 2 ** retryCount, TASK_SSE_MAX_RETRY_DELAY_MS)
    await abortableDelay(delay, signal)
  }
}

export function cancelTask(taskId: string, fetcher?: typeof fetch) {
  return apiRequest(`/api/v1/tasks/${taskId}/cancel`, CancelTaskApiV1TasksTaskIdCancelPostResponse, {
    method: 'POST',
    fetcher,
  })
}