import { expect, it, vi } from 'vitest'
import { getPlaybackSettings } from './playback'

it('fetches only the typed playback defaults', async () => {
  const data = { scene_pause_duration_ms: 1700, speaker_change_pause_duration_ms: 400, narrator_sentence_pause_duration_ms: 600, min_ms: 0, max_ms: 60000, step_ms: 100 }
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(data)))
  expect(await getPlaybackSettings(fetcher)).toEqual(data)
  expect(fetcher).toHaveBeenCalledWith('/api/v1/playback/settings', expect.objectContaining({ headers: { Accept: 'application/json' } }))
})
