import { describe, expect, it, vi } from 'vitest'

import { ApiClientError } from './client'
import { createVoice, deleteVoice, getVoice, getVoiceUsages, listVoices, previewVoice, VOICE_LIMIT_EXCEEDED } from './voices'

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

  it('レジストリ同期だけが失敗した場合は作成成功と保留状態を返す', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ speaker: 'Alice', voice_id: 'voice-a', voice_traits: '勇敢' }), {
        status: 200,
        headers: { 'X-Voice-Registry-Sync': 'pending' },
      }),
    )

    await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '勇敢' }, fetcher)).resolves.toMatchObject({
      voice_id: 'voice-a',
      registry_sync_pending: true,
    })
    expect(fetcher).toHaveBeenCalledTimes(1)
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

describe('voice library API', () => {
  const voice = {
    voice_id: 'voice-a',
    speaker_label: 'Alice',
    voice_traits: 'warm',
    language_code: 'ja-JP',
    created_at: null,
    created_at_source: 'unknown',
    expire_time: null,
    availability: 'available',
    expiry_state: 'unknown',
    last_checked_at: null,
    usage_count: 1,
  }

  it('一覧・詳細・利用先を取得し、Delete は一度だけ送る', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ voices: [voice], checked_at: null, complete: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(voice), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ usages: [{ document_id: 'doc-a', title: 'Story', speaker: 'Alice' }], complete: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await expect(listVoices(fetcher)).resolves.toMatchObject({ voices: [{ voice_id: 'voice-a' }], complete: true })
    await expect(getVoice('voice-a', fetcher)).resolves.toMatchObject({ availability: 'available' })
    await expect(getVoiceUsages('voice-a', fetcher)).resolves.toMatchObject({ usages: [{ document_id: 'doc-a' }], complete: true })
    await expect(deleteVoice('voice-a', fetcher)).resolves.toBeUndefined()

    expect(fetcher.mock.calls.map(([path, init]) => [path, init?.method ?? 'GET'])).toEqual([
      ['/api/v1/voices', 'GET'],
      ['/api/v1/voices/voice-a', 'GET'],
      ['/api/v1/voices/voice-a/usages', 'GET'],
      ['/api/v1/voices/voice-a', 'DELETE'],
    ])
    expect(fetcher).toHaveBeenCalledTimes(4)
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

it.each([false, true])('作成拒否502のcontextをそのまま届ける draft=%s', async (draft) => {
  const context = { operation: 'voice_design', reason: 'invalid_argument', provider_status: 400, provider_code: 'INVALID_ARGUMENT', retryable: false }
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'VALIDATION_ERROR', message: 'safe', context } }), { status: 502 }))
  await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '合成説明' }, fetcher, draft)).rejects.toMatchObject({ status: 502, code: 'VALIDATION_ERROR', context })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('contextのない503 prefixを既存wrapperで分類する', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'VALIDATION_ERROR', message: 'VOICE_PROVIDER_UNAVAILABLE: temporary', context: null } }), { status: 503 }))
  await expect(createVoice('doc-1', { speaker: 'Alice', voice_traits: '合成説明' }, fetcher)).rejects.toMatchObject({ status: 503, code: 'VOICE_PROVIDER_UNAVAILABLE' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('省略したstyleと言語は送らず、保存した読み方と言語を利用できる', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(new Uint8Array([0, 1]), { status: 200 }))
  await previewVoice(undefined, { voice_id: 'voice-a', text: 'Hello.' }, fetcher, true)
  const body = JSON.parse(fetcher.mock.calls[0][1].body)
  expect(body).not.toHaveProperty('style')
  expect(body).not.toHaveProperty('language_code')
})

it('明示した空styleと言語はそのまま送る', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(new Uint8Array([0, 1]), { status: 200 }))
  await previewVoice(undefined, { voice_id: 'voice-a', text: 'Hello.', style: '', language_code: 'en-US' }, fetcher, true)
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ style: '', language_code: 'en-US' })
})
