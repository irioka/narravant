import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, LoaderCircle, Pause, RefreshCw, Volume2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { DocumentDetail, Scene } from '@/api/contracts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { localizeUiError, type KnownErrorCode } from '@/i18n/errors'
import { base64ToUint8Array, pcm16ToAudioBuffer } from './audio-decoder'
import { playbackSourceAtPosition, type PlaybackStartPosition } from './fountain-scenes'

const MAX_SCHEDULED_SOURCES = 32

export type PlaybackStatus = 'idle' | 'generating' | 'playing' | 'stopped' | 'error' | 'completed'

export interface AudiobookPlayerProps {
  documentId: string
  scenes: Scene[]
  isPlaying: boolean
  onPlayingChange: (playing: boolean) => void
  disabled?: boolean
  /** 保存せずに再生する本文・声割当。文途中からの再開にも使う。 */
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
  sourceOffset?: number
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
  const { t: tErrors } = useTranslation('errors')
  const [status, setStatus] = useState<PlaybackStatus>('idle')
  const [deliverySetting, setDeliverySetting] = useState({ documentId, value: '' })
  // 作品の切替直後にも前作品の指定を送信しない。
  const narratorStyle = deliverySetting.documentId === documentId ? deliverySetting.value : ''
  const deliveryTooLong = Array.from(narratorStyle).length > 500
  const playbackActive = isPlaying || status === 'generating' || status === 'playing'
  const [errorMessage, setErrorMessage] = useState<string>()
  const [failedPosition, setFailedPosition] = useState<{ scene: number; utterance: number }>()
  const [currentSpeaker, setCurrentSpeaker] = useState<string>()
  const [currentSceneNumber, setCurrentSceneNumber] = useState<number>()
  const [currentUtteranceIndex, setCurrentUtteranceIndex] = useState<number>()

  const wsRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const scheduledSourcesRef = useRef(new Map<AudioBufferSourceNode, { highlightTimer?: ReturnType<typeof setTimeout> }>())
  const scheduledEndRef = useRef(0)
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
    for (const [source, scheduled] of scheduledSourcesRef.current) {
      if (scheduled.highlightTimer !== undefined) clearTimeout(scheduled.highlightTimer)
      source.onended = null
      try {
        source.stop()
      } catch {
        // 終了済みsourceは停止不要。
      }
      source.disconnect()
    }
    scheduledSourcesRef.current.clear()
    scheduledEndRef.current = 0
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
    // 先行合成が速くても、出力グラフと表示用timerを無制限に増やさない。
    // 終了通知では次の音声を即時再生せず、常に先行予約の空きだけ補充する。
    while (audioQueueRef.current.length > 0 && scheduledSourcesRef.current.size < MAX_SCHEDULED_SOURCES) {
      const item = audioQueueRef.current.shift()!
      isPlayingQueueRef.current = true
      setStatus('playing')

      const ctx = getAudioContext()
      if (!ctx) {
        // In non-audio environment (e.g. tests), advance to next item
        continue
      }

      try {
        const source = ctx.createBufferSource()
        source.buffer = item.buffer
        source.connect(ctx.destination)
        // onendedはメインスレッドで遅延するため、音声の接続時刻には使わない。
        const startAt = Math.max(ctx.currentTime, scheduledEndRef.current)
        source.onended = () => {
          const scheduled = scheduledSourcesRef.current.get(source)
          if (!scheduled) return
          if (scheduled.highlightTimer !== undefined) clearTimeout(scheduled.highlightTimer)
          scheduledSourcesRef.current.delete(source)
          source.disconnect()
          playNextRef.current()
        }
        source.start(startAt)
        scheduledEndRef.current = startAt + item.buffer.length / item.buffer.sampleRate
        const scheduled: { highlightTimer?: ReturnType<typeof setTimeout> } = {}
        scheduledSourcesRef.current.set(source, scheduled)

        const showPosition = () => {
          if (!scheduledSourcesRef.current.has(source)) return
          // timerの発火時刻ではなく音声時計で確認し、出力停止中に先走らない。
          const remainingMs = (startAt - ctx.currentTime) * 1000
          if (remainingMs > 0) {
            scheduled.highlightTimer = setTimeout(showPosition, Math.max(1, remainingMs))
            return
          }
          if (item.kind === 'speech' && item.utteranceIndex !== undefined) {
            setCurrentSpeaker(item.speaker)
            setCurrentSceneNumber(item.sceneNumber)
            setCurrentUtteranceIndex(item.utteranceIndex)
            onPlaybackPositionChange?.({
              sceneNumber: item.sceneNumber, utteranceIndex: item.utteranceIndex,
              ...(item.sourceOffset === undefined ? {} : { sourceOffset: item.sourceOffset }),
            })
          } else {
            // 間の最中も直前のキャラ名・位置を保ち、原稿のハイライトだけ解除する。
            onPlaybackPositionChange?.(undefined)
          }
        }
        // 先行予約した次発話のハイライトを、受信時に進めない。
        const delayMs = (startAt - ctx.currentTime) * 1000
        if (delayMs > 0) {
          scheduled.highlightTimer = setTimeout(showPosition, delayMs)
        } else {
          showPosition()
        }
      } catch {
        continue
      }
    }
    isPlayingQueueRef.current = scheduledSourcesRef.current.size > 0
    if (isPlayingQueueRef.current) return
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

  const startPlayback = useCallback((sceneNumber: number, utteranceIndex = 0, sourceOffset?: number) => {
    if (deliveryTooLong) return
    const requestedNarratorStyle = narratorStyle.trim()
    stopAudio()
    // ボタン操作中に出力を起動する。音声の到着まで待つと再生制限に掛かる場合がある。
    getAudioContext()
    setErrorMessage(undefined)
    setFailedPosition(undefined)
    setStatus('generating')
    onPlayingChange(true)

    const playbackPosition = sourceOffset === undefined
      ? { sceneNumber, utteranceIndex }
      : { sceneNumber, utteranceIndex, sourceOffset }
    const playbackDraft = draft && sourceOffset !== undefined
      ? { ...draft, source_fountain: playbackSourceAtPosition(draft.source_fountain, playbackPosition) }
      : draft
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const path = playbackDraft ? '/api/v1/documents/playback' : `/api/v1/documents/${documentId}/playback`
    const wsUrl = `${protocol}//${window.location.host}${path}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    const enqueueCurrentAudioSegment = () => {
      const chunks = currentChunksRef.current
      const context = getAudioContext()
      const meta = currentUtteranceMetaRef.current
      const byteLength = chunks.reduce((total, chunk) => total + chunk.byteLength, 0)
      if (byteLength < 2 || !context || !meta) return

      // PCM16のサンプル境界を保ち、分割された末尾1byteだけ次のchunkへ渡す。
      const bytes = new Uint8Array(byteLength)
      let offset = 0
      for (const chunk of chunks) {
        bytes.set(chunk, offset)
        offset += chunk.byteLength
      }
      const playableLength = byteLength - byteLength % 2
      currentChunksRef.current = playableLength < byteLength ? [bytes.slice(playableLength)] : []
      audioQueueRef.current.push({
        kind: 'speech',
        sceneNumber: meta.sceneNumber,
        utteranceIndex: meta.utteranceIndex,
        speaker: meta.speaker,
        // 切り詰めた最初の発話だけ、元原稿での再開位置を表示へ引き継ぐ。
        sourceOffset: playbackDraft && meta.sceneNumber === sceneNumber && meta.utteranceIndex === utteranceIndex
          ? sourceOffset : undefined,
        buffer: pcm16ToAudioBuffer([bytes.subarray(0, playableLength)], context, 24000),
      })
      playNextInQueue()
    }

    ws.onopen = () => {
      if (wsRef.current !== ws) return
      ws.send(JSON.stringify({
        action: 'start',
        ...(requestedNarratorStyle ? { narrator_style: requestedNarratorStyle } : {}),
        scene_number: sceneNumber,
        start_utterance_index: utteranceIndex,
        ...(playbackDraft ? { draft: playbackDraft } : {}),
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
        } else if (ev === 'scene_pause' || ev === 'utterance_pause') {
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
            playNextInQueue()
          }
        } else if (ev === 'audio_chunk') {
          if (msg.data) {
            currentChunksRef.current.push(base64ToUint8Array(msg.data))
            enqueueCurrentAudioSegment()
          }
        } else if (ev === 'audio_segment_end') {
          enqueueCurrentAudioSegment()
        } else if (ev === 'utterance_end') {
          // audio_segment_end を送らない旧 backend とも再生できるよう、残りをここで flush する。
          enqueueCurrentAudioSegment()
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
          const errorMessage = typeof msg.code === 'string' && msg.code.startsWith('VOICE_')
            ? localizeUiError(tErrors, { code: msg.code as KnownErrorCode }).message
            : msg.message || t('analysis:player.genericError')
          setErrorMessage(errorMessage)
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
  }, [deliveryTooLong, narratorStyle, documentId, draft, getAudioContext, onPlaybackPositionChange, onPlayingChange, playNextInQueue, stopAudio, t, tErrors])

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
    setDeliverySetting({ documentId, value: '' })
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
    startPlayback(start.sceneNumber, start.utteranceIndex, start.sourceOffset)
  }, [defaultSceneNumber, onBeforePlay, playbackStart, startPlayback])

  const retryPlayback = useCallback(() => {
    if (failedPosition) {
      startPlayback(failedPosition.scene, failedPosition.utterance)
    } else {
      const start = playbackStart ?? { sceneNumber: defaultSceneNumber, utteranceIndex: 0 }
      startPlayback(start.sceneNumber, start.utteranceIndex, start.sourceOffset)
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
      <div className="flex items-center gap-2">
        <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
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

        <div className="flex min-w-0 flex-1 items-center gap-2 text-xs">
          <label className="shrink-0 whitespace-nowrap" htmlFor="narrator-delivery">
            {t('analysis:player.narratorDelivery')}
          </label>
          <Input id="narrator-delivery" className="min-w-0 flex-1" value={narratorStyle}
            disabled={playbackActive}
            aria-invalid={deliveryTooLong}
            aria-describedby={deliveryTooLong ? 'narrator-delivery-error' : undefined}
            onChange={(event) => setDeliverySetting({ documentId, value: event.target.value })} />
          <Button className="shrink-0" variant="ghost" size="sm" disabled={playbackActive}
            onClick={() => setDeliverySetting({ documentId, value: '' })}>
            {t('analysis:player.clearNarratorDelivery')}
          </Button>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {isPlaying && status === 'generating' && (
            <div aria-live="polite" className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
              <span>{t('analysis:player.preparing')}</span>
            </div>
          )}

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

      {deliveryTooLong && (
        <p id="narrator-delivery-error" className="text-xs text-destructive" role="alert">
          {t('analysis:player.narratorDeliveryTooLong')}
        </p>
      )}

      {playbackActive && (
        <div className="min-h-6 border-t pt-2 text-xs text-muted-foreground" data-testid="playback-position">
          {currentSpeaker && currentSceneNumber !== undefined && currentUtteranceIndex !== undefined && (
            <>
              <span>{currentSpeaker}</span>{' '}
              <span>{t('analysis:player.utterancePosition', { scene: currentSceneNumber, utterance: currentUtteranceIndex + 1 })}</span>
            </>
          )}
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
