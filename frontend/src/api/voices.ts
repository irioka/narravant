import { ApiClientError, apiErrorFromPayload, apiRequest } from './client'
import { CreateDocumentVoiceApiV1DocumentsDocumentIdVoicesPostResponse } from './generated/contracts'

/** Distinguishable error code for the voice-creation quota limit. */
export const VOICE_LIMIT_EXCEEDED = 'VOICE_LIMIT_EXCEEDED'

/** Distinguishable error code for a temporary provider outage (retry later). */
export const VOICE_PROVIDER_UNAVAILABLE = 'VOICE_PROVIDER_UNAVAILABLE'

export interface CreateVoiceInput {
  speaker: string
  voice_traits: string
  language_code?: string
}

export interface CreateVoiceResult {
  speaker: string
  voice_id: string
  voice_traits: string
}

export interface PreviewVoiceInput {
  voice_id: string
  text: string
  style?: string
  language_code?: string
}

/**
 * Create a custom voice persona (billable Voice Design call).
 *
 * On a voice quota limit the backend returns HTTP 409 whose detail starts with
 * "VOICE_LIMIT_EXCEEDED:"; that is surfaced as an {@link ApiClientError} whose
 * `code` is {@link VOICE_LIMIT_EXCEEDED} so callers can show the over-limit dialog.
 */
export async function createVoice(
  documentId: string | undefined,
  input: CreateVoiceInput,
  fetcher?: typeof fetch,
  draft = false,
): Promise<CreateVoiceResult> {
  try {
    return await apiRequest(
      draft ? '/api/v1/voices' : `/api/v1/documents/${documentId}/voices`,
      CreateDocumentVoiceApiV1DocumentsDocumentIdVoicesPostResponse,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          speaker: input.speaker,
          voice_traits: input.voice_traits,
          language_code: input.language_code ?? 'ja-JP',
        }),
        fetcher,
      },
    )
  } catch (error) {
    if (isVoiceLimitExceeded(error)) {
      throw new ApiClientError(409, VOICE_LIMIT_EXCEEDED, error.message, error.context)
    }
    if (isVoiceProviderUnavailable(error)) {
      throw new ApiClientError(503, VOICE_PROVIDER_UNAVAILABLE, error.message, error.context)
    }
    throw error
  }
}

function isVoiceLimitExceeded(error: unknown): error is ApiClientError {
  if (!(error instanceof ApiClientError) || error.status !== 409) return false
  // FastAPI HTTPException(detail=...) surfaces via the error message; the detail
  // string starts with "VOICE_LIMIT_EXCEEDED:".
  return error.code === VOICE_LIMIT_EXCEEDED || error.message.startsWith(VOICE_LIMIT_EXCEEDED)
}

function isVoiceProviderUnavailable(error: unknown): error is ApiClientError {
  if (!(error instanceof ApiClientError) || error.status !== 503) return false
  // The shared envelope maps 503 to a generic code, so the distinct marker is
  // carried by the detail string prefix "VOICE_PROVIDER_UNAVAILABLE:".
  return error.code === VOICE_PROVIDER_UNAVAILABLE || error.message.startsWith(VOICE_PROVIDER_UNAVAILABLE)
}

/**
 * Synthesize a short sample line with an existing voice and return the raw PCM
 * bytes (audio/l16; rate=24000; channels=1) for client-side playback.
 */
export async function previewVoice(
  documentId: string | undefined,
  input: PreviewVoiceInput,
  fetcher: typeof fetch = fetch,
  draft = false,
): Promise<Uint8Array> {
  const response = await fetcher(draft ? '/api/v1/voices/preview' : `/api/v1/documents/${documentId}/voices/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'audio/l16, */*' },
    body: JSON.stringify({
      voice_id: input.voice_id,
      text: input.text,
      style: input.style ?? '',
      language_code: input.language_code ?? 'ja-JP',
    }),
  })

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => undefined)
    throw apiErrorFromPayload(response.status, payload, 'VOICE_PREVIEW_FAILED', 'Failed to retrieve the preview audio.')
  }

  const buffer = await response.arrayBuffer()
  return new Uint8Array(buffer)
}
