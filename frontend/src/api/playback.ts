import { z } from 'zod'
import { apiRequest } from './client'
import { GetPlaybackSettingsApiV1PlaybackSettingsGetResponse } from './generated/contracts'

export type PlaybackSettings = z.infer<typeof GetPlaybackSettingsApiV1PlaybackSettingsGetResponse>
export const PAUSE_FIELDS = ['scene_pause_duration_ms', 'speaker_change_pause_duration_ms', 'narrator_sentence_pause_duration_ms'] as const
export type PlaybackPauseOverrides = Pick<PlaybackSettings, typeof PAUSE_FIELDS[number]>

export function getPlaybackSettings(fetcher?: typeof fetch): Promise<PlaybackSettings> {
  return apiRequest('/api/v1/playback/settings', GetPlaybackSettingsApiV1PlaybackSettingsGetResponse, { fetcher })
}
