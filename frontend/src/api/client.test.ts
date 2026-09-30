import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { ApiClientError, apiErrorFromPayload, apiRequest } from './client'

describe('apiRequest', () => {
  it('AuthorizationやCookieを付与せず検証済みJSONを返す', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'doc-1' }), { status: 200 }))

    await expect(apiRequest('/api/v1/documents/doc-1', z.object({ id: z.string() }), { fetcher })).resolves.toEqual({
      id: 'doc-1',
    })
    expect(fetcher).toHaveBeenCalledWith('/api/v1/documents/doc-1', expect.objectContaining({
      headers: expect.objectContaining({ Accept: 'application/json' }),
    }))
    expect(fetcher.mock.calls[0][1]?.headers).not.toHaveProperty('Authorization')
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty('credentials')
  })

  it('HTTPエラーをstatus、code、context付き例外に変換する', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: 'CONFLICT', message: '競合しています。', context: { actual_version: 4 } } }),
        { status: 409 },
      ),
    )

    await expect(apiRequest('/api/v1/documents/doc-1', z.object({}), { fetcher })).rejects.toMatchObject({
      status: 409,
      code: 'CONFLICT',
      message: '競合しています。',
      context: { actual_version: 4 },
    })
  })

  it('成功responseの契約不一致をCONTRACT_MISMATCHに変換する', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ unexpected: true }), { status: 200 }))

    await expect(apiRequest('/api/v1/documents/doc-1', z.object({ id: z.string() }), { fetcher })).rejects.toMatchObject({
      status: 200,
      code: 'CONTRACT_MISMATCH',
    })
  })
})

describe('apiErrorFromPayload', () => {
  it('valid envelopeからstatus、code、message、contextを保持する', () => {
    const error = apiErrorFromPayload(404, {
      error: {
        code: 'DOCUMENT_NOT_FOUND',
        message: '指定されたドキュメントが見つかりません。',
        context: { doc_id: 'doc-1' },
      },
    })
    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(404)
    expect(error.code).toBe('DOCUMENT_NOT_FOUND')
    expect(error.message).toBe('指定されたドキュメントが見つかりません。')
    expect(error.context).toEqual({ doc_id: 'doc-1' })
  })

  it('malformed payloadは指定fallback code/messageを使用する', () => {
    const error = apiErrorFromPayload(500, { invalid: 'shape' }, 'CUSTOM_FALLBACK', 'カスタムエラー')
    expect(error.status).toBe(500)
    expect(error.code).toBe('CUSTOM_FALLBACK')
    expect(error.message).toBe('カスタムエラー')
    expect(error.context).toBeUndefined()
  })

  it('401 payloadもApiClientErrorにパースする', () => {
    const error = apiErrorFromPayload(401, { error: { code: 'UNAUTHENTICATED', message: '認証が必要です。' } })
    expect(error.status).toBe(401)
    expect(error.code).toBe('UNAUTHENTICATED')
    expect(error.message).toBe('認証が必要です。')
  })
})
