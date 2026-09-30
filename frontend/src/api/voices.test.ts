import { describe, expect, it, vi } from 'vitest'

import { ApiClientError } from './client'
import { createVoice, previewVoice, VOICE_LIMIT_EXCEEDED } from './voices'

describe('createVoice', () => {
  it('作成済みの voice_id を返す', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ speaker: 'Alice', voice_id: 'voice-1', voice_traits: '勇敢' }), { status: 200 }),
    )

    await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '勇敢' }, fetcher)).resolves.toEqual({
      speaker: 'Alice',
      voice_id: 'voice-1',
      voice_traits: '勇敢',
    })
    expect(fetcher).toHaveBeenCalledWith('/api/v1/documents/doc-1/voices', expect.objectContaining({ method: 'POST' }))
  })

  it('未保存draftでは文書認証なしの一時voice endpointを使う', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ speaker: 'Alice', voice_id: 'voice-draft', voice_traits: '勇敢' }), { status: 200 }),
    )

    await expect(createVoice('draft-id', { speaker: 'Alice', voice_traits: '勇敢' }, fetcher, true)).resolves.toMatchObject({
      voice_id: 'voice-draft',
    })
    expect(fetcher).toHaveBeenCalledWith('/api/v1/voices', expect.objectContaining({ method: 'POST' }))
  })

  it('声の上限エラー(409)を VOICE_LIMIT_EXCEEDED コードにマップする', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: 'CONFLICT', message: 'VOICE_LIMIT_EXCEEDED: 上限に達しました。' } }),
        { status: 409 },
      ),
    )

    await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '勇敢' }, fetcher)).rejects.toMatchObject({
      code: VOICE_LIMIT_EXCEEDED,
      status: 409,
    })
  })

  it('その他のエラーはそのまま伝播する', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'INTERNAL_ERROR', message: '失敗' } }), { status: 500 }),
    )

    await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '勇敢' }, fetcher)).rejects.toMatchObject({
      code: 'INTERNAL_ERROR',
    })
  })
})

describe('previewVoice', () => {
  it('生の PCM バイト列(Uint8Array)を返す', async () => {
    const pcm = new Uint8Array([0, 1, 2, 3])
    const fetcher = vi.fn().mockResolvedValue(
      new Response(pcm, { status: 200, headers: { 'content-type': 'audio/l16; rate=24000; channels=1' } }),
    )

    const result = await previewVoice('doc-1', { voice_id: 'voice-1', text: 'こんにちは' }, fetcher)
    expect(result).toBeInstanceOf(Uint8Array)
    expect(Array.from(result)).toEqual([0, 1, 2, 3])
  })

  it('失敗時は ApiClientError を投げる', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'INTERNAL_ERROR', message: '失敗' } }), { status: 502 }),
    )

    await expect(previewVoice('doc-1', { voice_id: 'voice-1', text: 'x' }, fetcher)).rejects.toBeInstanceOf(ApiClientError)
  })
})
