import { expect, it, vi } from 'vitest'
import { importDocument } from './documents'

it('Importはlocaleを追加せずfileだけを送る', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
    JSON.stringify({ task_id: 'synthetic-import', status: 'queued', task_type: 'document_import' }),
    { status: 202, headers: { 'Content-Type': 'application/json' } },
  ))
  await importDocument(new File(['Synthetic story.'], 'synthetic.txt'), fetcher)
  const [, options] = fetcher.mock.calls[0]
  const body = options?.body
  expect(body).toBeInstanceOf(FormData)
  expect([...(body as FormData).keys()]).toEqual(['file'])
  const headers = options?.headers as Record<string, string> | undefined
  expect(headers?.['Accept-Language']).toBeUndefined()
  expect(headers?.['Content-Language']).toBeUndefined()
  expect(options?.headers).toEqual({ Accept: 'application/json' })
})
