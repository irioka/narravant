import { describe, expect, it, vi } from 'vitest'

import { reanalyzeEmotionArc, reanalyzeImportDraftEmotionArc } from './documents'

describe('reanalyzeEmotionArc', () => {
  const acceptedResponse = () => new Response(JSON.stringify({
    task_id: 'synthetic-task',
    task_type: 'emotion_arc_reanalysis',
    status: 'queued',
  }), { status: 202, headers: { 'Content-Type': 'application/json' } })

  it('sends the current editor Fountain as an optional request body', async () => {
    const fetcher = vi.fn().mockResolvedValue(acceptedResponse())
    const sourceFountain = 'INT. SYNTHETIC ROOM - DAY\n\n@ALICE\nSynthetic line.'

    await reanalyzeEmotionArc('synthetic-document', sourceFountain, fetcher)

    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/documents/synthetic-document/emotion-arc/reanalyze',
      expect.objectContaining({
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_fountain: sourceFountain }),
      }),
    )
  })

  it('preserves body-free requests for existing callers', async () => {
    const fetcher = vi.fn().mockResolvedValue(acceptedResponse())

    await reanalyzeEmotionArc('synthetic-document', undefined, fetcher)

    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/documents/synthetic-document/emotion-arc/reanalyze',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty('body')
  })
})

describe('reanalyzeImportDraftEmotionArc', () => {
  it('sends the edited import draft Fountain to its owner-scoped reanalysis endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      task_id: 'synthetic-task', task_type: 'emotion_arc_reanalysis', status: 'queued',
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    const sourceFountain = 'INT. SYNTHETIC IMPORT - NIGHT\n\n@ALICE\nSynthetic line.'

    await reanalyzeImportDraftEmotionArc('synthetic-import-task', sourceFountain, fetcher)

    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/documents/import/synthetic-import-task/emotion-arc/reanalyze',
      expect.objectContaining({
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_fountain: sourceFountain }),
      }),
    )
  })
})
