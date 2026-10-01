import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Pause, RefreshCw, Volume2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { DocumentDetail, Scene } from '@/api/contracts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { base64ToUint8Array, pcm16ToAudioBuffer } from './audio-decoder'
import type { PlaybackStartPosition } from './fountain-scenes'

export type PlaybackStatus = 'idle' | 'generating' | 'playing' | 'stopped' | 'error' | 'completed'

export interface AudiobookPlayerProps {
  documentId: string
  scenes: Scene[]
  isPlaying: boolean
  onPlayingChange: (playing: boolean) => void
  disabled?: boolean
  /** Import直後の下書きは、本文と声の割り当てを保存せず再生する。 */
  draft?: Pick<DocumentDetail, 'source_fountain' | 'voice_assignments'>
  /** Optional guard run before playback starts; return false to block playback. */
  onBeforePlay?: () => boolean
  /** Current script cursor mapped to the first utterance to play. */
  playbackStart?: PlaybackStartPosition
  onSceneJump?: (sceneNumber: number) => void
  /** Reports an utterance when its audio buffer actually starts playing. */
  onPlaybackPositionChange?: (position: PlaybackStartPosition | undefined) => void
  /** Increment to stop an active playback from an operation outside the player. */
  stopSignal?: number
}

interface QueuedAudio {
  kind: 'speech' | 'pause'
  sceneNumber: number
  utteranceIndex?: number
  speaker?: string
  buffer: AudioBuffer
}

export function AudiobookPlayer({
  documentId,
  scenes,
  isPlaying,
  onPlayingChange,
  disabled = false,
  draft,
  onBeforePlay,
  playbackStart,
  onSceneJump,
  onPlaybackPositionChange,
  stopSignal,
}: AudiobookPlayerProps) {
  const { t } = useTranslation(['analysis'])
  const [status, setStatus] = useState<PlaybackStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string>()
  const [failedPosition, setFailedPosition] = useState<{ scene: number; utterance: number }>()
  const [currentSpeaker, setCurrentSpeaker] = useState<string>()
  const [currentSceneNumber, setCurrentSceneNumber] = useState<number>()
  const [currentUtteranceIndex, setCurrentUtteranceIndex] = useState<number>()

  const wsRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const activeSourceRef = useRef<AudioBufferSourceNode | null>(null)
  const audioQueueRef = useRef<QueuedAudio[]>([])
  const isPlayingQueueRef = useRef<boolean>(false)
  const streamCompleteRef = useRef(false)
  const currentChunksRef = useRef<Uint8Array[]>([])
  const currentUtteranceMetaRef = useRef<{ speaker: string; sceneNumber: number; utteranceIndex: number } | undefined>(undefined)
  const sceneNumbers = useMemo(() => scenes.length > 0 ? scenes.map((scene) => scene.scene_number) : [1], [scenes])
  const defaultSceneNumber = sceneNumbers[0] ?? 1
  const sceneInputValue = String(playbackStart?.sceneNumber ?? defaultSceneNumber)
  const sceneInputRef = useRef<HTMLInputElement>(null)

  const commitSceneInput = useCallback((rawValue: string, input: HTMLInputElement) => {
    const requested = Number.parseInt(rawValue, 10)
    if (!Number.isFinite(requested)) {
      input.value = sceneInputValue
      return
    }
    const target = sceneNumbers.find((sceneNumber) => sceneNumber === requested)
    if (target === undefined) {
      input.value = sceneInputValue
      return
    }
    input.value = String(target)
    onSceneJump?.(target)
  }, [onSceneJump, sceneInputValue, sceneNumbers])

  // Select and scroll to the scene heading as soon as the field is focused or its
  // value maps to a real scene, without waiting for blur/Enter. Invalid or partial
  // input is left untouched so the user can keep typing.
  const jumpToSceneInput = useCallback((rawValue: string) => {
    const requested = Number.parseInt(rawValue, 10)
    if (!Number.isFinite(requested)) return
    const target = sceneNumbers.find((sceneNumber) => sceneNumber === requested)
    if (target === undefined) return
    onSceneJump?.(target)
  }, [onSceneJump, sceneNumbers])

  // Step to the adjacent scene in document order. `offset` is an index delta into
  // the ordered scene list (not scene-number arithmetic), so non-contiguous scene
  // numbers still move one scene at a time. The up triangle uses offset -1
  // (previous/earlier scene); the down triangle uses +1 (next/later scene).
  const stepScene = useCallback((offset: number) => {
    const current = playbackStart?.sceneNumber ?? defaultSceneNumber
    const currentIndex = sceneNumbers.indexOf(current)
    const baseIndex = currentIndex === -1 ? 0 : currentIndex
    const nextIndex = baseIndex + offset
    if (nextIndex < 0 || nextIndex >= sceneNumbers.length) return
    const target = sceneNumbers[nextIndex]
    if (sceneInputRef.current) sceneInputRef.current.value = String(target)
    onSceneJump?.(target)
  }, [defaultSceneNumber, onSceneJump, playbackStart?.sceneNumber, sceneNumbers])

  // Mirror the current scene into the uncontrolled field when it changes from the
  // outside (e.g. chart click, playback position). Skip while the field is focused
  // so the user's in-progress typing is not overwritten.
  useEffect(() => {
    const input = sceneInputRef.current
    if (!input || window.document.activeElement === input) return
    input.value = sceneInputValue
  }, [sceneInputValue])

  const getAudioContext = useCallback(() => {
    if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      if (AudioCtx) {
        audioContextRef.current = new AudioCtx({ sampleRate: 24000 })
      }
    }
    if (audioContextRef.current?.state === 'suspended') {
      void audioContextRef.current.resume()
    }
    return audioContextRef.current
  }, [])

  const stopAudio = useCallback(() => {
    if (activeSourceRef.current) {
      try {
        activeSourceRef.current.stop()
      } catch {
        // already stopped
      }
      activeSourceRef.current = null
    }
    audioQueueRef.current = []
    isPlayingQueueRef.current = false
    streamCompleteRef.current = false
    currentChunksRef.current = []
    currentUtteranceMetaRef.current = undefined
    setCurrentSpeaker(undefined)
    setCurrentSceneNumber(undefined)
    setCurrentUtteranceIndex(undefined)
    onPlaybackPositionChange?.(undefined)
  }, [onPlaybackPositionChange])

  const playNextRef = useRef<() => void>(() => { })

  const playNextInQueue = useCallback(() => {
    while (audioQueueRef.current.length > 0) {
      const item = audioQueueRef.current.shift()!
      isPlayingQueueRef.current = true
      setStatus('playing')

      if (item.kind === 'speech' && item.utteranceIndex !== undefined) {
        setCurrentSpeaker(item.speaker)
        setCurrentSceneNumber(item.sceneNumber)
        setCurrentUtteranceIndex(item.utteranceIndex)
        onPlaybackPositionChange?.({ sceneNumber: item.sceneNumber, utteranceIndex: item.utteranceIndex })
      } else {
        setCurrentSpeaker(undefined)
        setCurrentSceneNumber(undefined)
        setCurrentUtteranceIndex(undefined)
        onPlaybackPositionChange?.(undefined)
      }

      const ctx = getAudioContext()
      if (!ctx) {
        // In non-audio environment (e.g. tests), advance to next item
        continue
      }

      try {
        const source = ctx.createBufferSource()
        source.buffer = item.buffer
        source.connect(ctx.destination)
        source.onended = () => {
          activeSourceRef.current = null
          playNextRef.current()
        }
        activeSourceRef.current = source
        source.start(0)
        return
      } catch {
        continue
      }
    }
    isPlayingQueueRef.current = false
    if (streamCompleteRef.current && audioQueueRef.current.length === 0) {
      streamCompleteRef.current = false
      setStatus('completed')
      setCurrentSpeaker(undefined)
      setCurrentSceneNumber(undefined)
      setCurrentUtteranceIndex(undefined)
      onPlaybackPositionChange?.(undefined)
      onPlayingChange(false)
    }
  }, [getAudioContext, onPlaybackPositionChange, onPlayingChange])

  useEffect(() => {
    playNextRef.current = playNextInQueue
  }, [playNextInQueue])

  const startPlayback = useCallback((sceneNumber: number, utteranceIndex = 0) => {
    stopAudio()
    // ボタン操作中に出力を起動する。音声の到着まで待つと再生制限に掛かる場合がある。
    getAudioContext()
    setErrorMessage(undefined)
    setFailedPosition(undefined)
    setStatus('generating')
    onPlayingChange(true)

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const path = draft ? '/api/v1/documents/playback' : `/api/v1/documents/${documentId}/playback`
    const wsUrl = `${protocol}//${window.location.host}${path}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      if (wsRef.current !== ws) return
      ws.send(JSON.stringify({
        action: 'start',
        scene_number: sceneNumber,
        start_utterance_index: utteranceIndex,
        ...(draft ? { draft } : {}),
      }))
    }

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return
      try {
        const msg = JSON.parse(event.data)
        const ev = msg.event

        if (ev === 'utterance_start') {
          currentChunksRef.current = []
          currentUtteranceMetaRef.current = {
            speaker: String(msg.speaker ?? ''),
            sceneNumber: Number(msg.scene_number),
            utteranceIndex: Number(msg.utterance_index),
          }
        } else if (ev === 'scene_pause') {
          const ctx = getAudioContext()
          if (ctx) {
            const rawDurationMs = Number(msg.duration_ms)
            const durationMs = Number.isFinite(rawDurationMs) && rawDurationMs >= 0 ? rawDurationMs : 3000
            const frameCount = Math.max(1, Math.round(ctx.sampleRate * durationMs / 1000))
            audioQueueRef.current.push({
              kind: 'pause',
              sceneNumber: Number(msg.scene_number),
              buffer: ctx.createBuffer(1, frameCount, ctx.sampleRate),
            })
            if (!isPlayingQueueRef.current) {
              playNextInQueue()
            }
          }
        } else if (ev === 'audio_chunk') {
          if (msg.data) {
            currentChunksRef.current.push(base64ToUint8Array(msg.data))
          }
        } else if (ev === 'utterance_end') {
          const ctx = getAudioContext()
          if (currentChunksRef.current.length > 0 && ctx) {
            const buffer = pcm16ToAudioBuffer(currentChunksRef.current, ctx, 24000)
            audioQueueRef.current.push({
              kind: 'speech',
              sceneNumber: msg.scene_number,
              utteranceIndex: msg.utterance_index,
              speaker: currentUtteranceMetaRef.current?.speaker,
              buffer,
            })
            if (!isPlayingQueueRef.current) {
              playNextInQueue()
            }
          }
          currentChunksRef.current = []
          currentUtteranceMetaRef.current = undefined
        } else if (ev === 'playback_complete') {
          // Playback finished streaming from server
          streamCompleteRef.current = true
          if (!isPlayingQueueRef.current && audioQueueRef.current.length === 0) {
            streamCompleteRef.current = false
            setStatus('completed')
            setCurrentSpeaker(undefined)
            setCurrentSceneNumber(undefined)
            setCurrentUtteranceIndex(undefined)
            onPlaybackPositionChange?.(undefined)
            onPlayingChange(false)
          }
        } else if (ev === 'error') {
          stopAudio()
          setStatus('error')
          setErrorMessage(msg.message || t('analysis:player.genericError'))
          if (msg.scene_number !== undefined && msg.utterance_index !== undefined) {
            setFailedPosition({
              scene: Number(msg.scene_number),
              utterance: Number(msg.utterance_index),
            })
          }
          onPlayingChange(false)
          ws.close()
        }
      } catch (err) {
        console.error('Error handling WebSocket message', err)
      }
    }

    ws.onerror = () => {
      if (wsRef.current !== ws) return
      stopAudio()
      setStatus('error')
      setErrorMessage(t('analysis:player.connectionError'))
      onPlayingChange(false)
    }

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null
    }
  }, [documentId, draft, getAudioContext, onPlaybackPositionChange, onPlayingChange, playNextInQueue, stopAudio, t])

  const stopPlayback = useCallback(() => {
    const ws = wsRef.current
    if (ws) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'stop' }))
      }
      if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) {
        ws.close()
      }
      wsRef.current = null
    }
    stopAudio()
    setStatus('stopped')
    onPlayingChange(false)
  }, [onPlayingChange, stopAudio])

  const previousDocumentIdRef = useRef(documentId)
  useEffect(() => {
    if (previousDocumentIdRef.current === documentId) return
    previousDocumentIdRef.current = documentId
    stopPlayback()
  }, [documentId, stopPlayback])

  const previousStopSignalRef = useRef(stopSignal)
  useEffect(() => {
    if (stopSignal === undefined || previousStopSignalRef.current === stopSignal) return
    previousStopSignalRef.current = stopSignal
    stopPlayback()
  }, [stopPlayback, stopSignal])

  const handlePlayClick = useCallback(() => {
    if (onBeforePlay && !onBeforePlay()) return
    const start = playbackStart ?? { sceneNumber: defaultSceneNumber, utteranceIndex: 0 }
    startPlayback(start.sceneNumber, start.utteranceIndex)
  }, [defaultSceneNumber, onBeforePlay, playbackStart, startPlayback])

  const retryPlayback = useCallback(() => {
    if (failedPosition) {
      startPlayback(failedPosition.scene, failedPosition.utterance)
    } else {
      const start = playbackStart ?? { sceneNumber: defaultSceneNumber, utteranceIndex: 0 }
      startPlayback(start.sceneNumber, start.utteranceIndex)
    }
  }, [defaultSceneNumber, failedPosition, playbackStart, startPlayback])

  useEffect(() => {
    return () => {
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
      stopAudio()
      // HMR でも cleanup が走るため、閉じた出力を次の再生に残さない。
      const context = audioContextRef.current
      audioContextRef.current = null
      if (context && context.state !== 'closed') void context.close()
    }
  }, [stopAudio])

  return (
    <div className="flex flex-col gap-2 rounded-md border bg-card p-3 shadow-xs" data-testid="audiobook-player">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-48 items-center gap-2 text-xs text-muted-foreground">
          <label className="whitespace-nowrap" htmlFor="playback-scene-number">{t('analysis:player.sceneNumber')}</label>
          <Input
            aria-label={t('analysis:player.sceneNumberAria')}
            className="w-16 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            disabled={isPlaying || disabled || sceneNumbers.length === 0}
            id="playback-scene-number"
            inputMode="numeric"
            max={Math.max(...sceneNumbers)}
            min={Math.min(...sceneNumbers)}
            onBlur={(event) => commitSceneInput(event.currentTarget.value, event.currentTarget)}
            onChange={(event) => jumpToSceneInput(event.currentTarget.value)}
            onFocus={(event) => jumpToSceneInput(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') commitSceneInput(event.currentTarget.value, event.currentTarget)
            }}
            ref={sceneInputRef}
            type="number"
            defaultValue={sceneInputValue}
          />
          <div className="flex flex-col">
            <Button
              aria-label={t('analysis:player.previousScene')}
              className="h-4 w-6 rounded-b-none p-0"
              disabled={isPlaying || disabled || sceneNumbers.length === 0}
              onClick={() => stepScene(-1)}
              size="icon"
              variant="outline"
            >
              <ChevronUp className="size-3" />
            </Button>
            <Button
              aria-label={t('analysis:player.nextScene')}
              className="h-4 w-6 rounded-t-none border-t-0 p-0"
              disabled={isPlaying || disabled || sceneNumbers.length === 0}
              onClick={() => stepScene(1)}
              size="icon"
              variant="outline"
            >
              <ChevronDown className="size-3" />
            </Button>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isPlaying ? (
            <Button
              aria-label={t('analysis:player.stopAria')}
              onClick={stopPlayback}
              size="sm"
              variant="outline"
            >
              <Pause className="mr-1.5 size-3.5" />
              {t('analysis:player.stop')}
            </Button>
          ) : (
            <Button
              aria-label={t('analysis:player.playAria')}
              disabled={disabled}
              onClick={handlePlayClick}
              size="sm"
              variant="default"
            >
              <Volume2 className="mr-1.5 size-3.5" />
              {t('analysis:player.play')}
            </Button>
          )}

          {status === 'error' && failedPosition && (
            <Button
              aria-label={t('analysis:player.retryAria')}
              onClick={retryPlayback}
              size="sm"
              variant="secondary"
            >
              <RefreshCw className="mr-1.5 size-3.5" />
              {t('analysis:player.retry')}
            </Button>
          )}
        </div>
      </div>

      {isPlaying && currentSpeaker && currentSceneNumber !== undefined && currentUtteranceIndex !== undefined && (
        <div className="border-t pt-2 text-xs text-muted-foreground">
          <span>{t('analysis:player.speaker', { speaker: currentSpeaker })}</span>{' '}
          <span>{t('analysis:player.utterancePosition', { scene: currentSceneNumber, utterance: currentUtteranceIndex + 1 })}</span>
        </div>
      )}

      {status === 'completed' && (
        <div className="flex items-center gap-1.5 border-t pt-2 text-xs text-green-600 dark:text-green-400">
          <CheckCircle2 className="size-3.5" />
          <span>{t('analysis:player.completed')}</span>
        </div>
      )}

      {status === 'error' && errorMessage && (
        <div className="mt-1 flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-2.5 text-xs text-destructive">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <div className="text-xs leading-5">
            {errorMessage}
            {failedPosition && (
              <span className="ml-1 font-mono text-[11px]">
                {t('analysis:player.failedPosition', { scene: failedPosition.scene, utterance: failedPosition.utterance + 1 })}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
