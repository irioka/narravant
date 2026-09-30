import { z } from 'zod'

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly context: Record<string, unknown> | undefined

  constructor(
    status: number,
    code: string,
    message: string,
    context?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = code
    this.context = context
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: BodyInit | null
  fetcher?: typeof fetch
}

const errorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    context: z.record(z.string(), z.unknown()).optional(),
  }),
})

export function apiErrorFromPayload(
  status: number,
  payload: unknown,
  fallbackCode = 'HTTP_ERROR',
  fallbackMessage = 'The request failed.',
): ApiClientError {
  const error = errorSchema.safeParse(payload)
  if (error.success) {
    return new ApiClientError(
      status,
      error.data.error.code,
      error.data.error.message,
      error.data.error.context,
    )
  }
  return new ApiClientError(status, fallbackCode, fallbackMessage)
}

export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  { fetcher = fetch, headers, ...options }: RequestOptions = {},
): Promise<T> {
  const response = await fetcher(path, {
    headers: { Accept: 'application/json', ...headers },
    ...options,
  })
  const payload: unknown = response.status === 204 ? undefined : await response.json().catch(() => undefined)

  if (!response.ok) {
    throw apiErrorFromPayload(response.status, payload)
  }

  try {
    return schema.parse(payload)
  } catch (error) {
    if (error instanceof z.ZodError) {
      throw new ApiClientError(response.status, 'CONTRACT_MISMATCH', 'The API response shape does not match the contract.')
    }
    throw error
  }
}
