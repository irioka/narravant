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
const MAX_BUFFERED_SECONDS = 32
const MAX_BUFFERED_ITEMS = 4096
const MAX_PCM_BUFFER_BYTES = 48000

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
  acknowledge: () => void
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
  const [failedPosition, setFailedPosition] = useState<{ scene: number; utterance: number; sourceOffset?: number }>()
  const [currentSpeaker, setCurrentSpeaker] = useState<string>()
  const [currentSceneNumber, setCurrentSceneNumber] = useState<number>()
  const [currentUtteranceIndex, setCurrentUtteranceIndex] = useState<number>()

  const wsRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const scheduledSourcesRef = useRef(new Map<AudioBufferSourceNode, { highlightTimer?: ReturnType<typeof setTimeout> }>())
  const scheduledEndRef = useRef(0)
  const audioQueueRef = useRef<QueuedAudio[]>([])
  const bufferedSecondsRef = useRef(0)
  const bufferWaitStartedRef = useRef<number | undefined>(undefined)
  const lastReleaseTimingRef = useRef({ releaseMs: 0, callbackLagMs: 0 })
  const lastAudiblePositionRef = useRef<PlaybackStartPosition | undefined>(undefined)
  const isPlayingQueueRef = useRef<boolean>(false)
  const streamCompleteRef = useRef(false)
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
    bufferedSecondsRef.current = 0
    bufferWaitStartedRef.current = undefined
    lastReleaseTimingRef.current = { releaseMs: 0, callbackLagMs: 0 }
    isPlayingQueueRef.current = false
    streamCompleteRef.current = false
    currentUtteranceMetaRef.current = undefined
    setCurrentSpeaker(undefined)
    setCurrentSceneNumber(undefined)
    setCurrentUtteranceIndex(undefined)
    onPlaybackPositionChange?.(undefined)
  }, [onPlaybackPositionChange])

  const failPlayback = useCallback((message: string) => {
    const resume = lastAudiblePositionRef.current
    const ws = wsRef.current
    wsRef.current = null
    stopAudio()
    setStatus('error')
    setErrorMessage(message)
    if (resume) {
      setFailedPosition({ scene: resume.sceneNumber, utterance: resume.utteranceIndex, sourceOffset: resume.sourceOffset })
    }
    onPlayingChange(false)
    ws?.close()
  }, [onPlayingChange, stopAudio])

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
          const releaseStarted = performance.now()
          const endAt = startAt + item.buffer.length / item.buffer.sampleRate
          const callbackLagMs = Math.max(0, (ctx.currentTime - endAt) * 1000)
          if (scheduled.highlightTimer !== undefined) clearTimeout(scheduled.highlightTimer)
          scheduledSourcesRef.current.delete(source)
          source.onended = null
          source.disconnect()
          bufferedSecondsRef.current = Math.max(0, bufferedSecondsRef.current - item.buffer.length / item.buffer.sampleRate)
          const releaseMs = performance.now() - releaseStarted
          lastReleaseTimingRef.current = { releaseMs, callbackLagMs }
          item.acknowledge()
          playNextRef.current()
          const callbackMs = performance.now() - releaseStarted
          if (releaseMs >= 100 || callbackMs >= 100 || callbackLagMs >= 250) {
            console.warn('Playback audio callback delayed', {
              release_ms: Math.round(releaseMs), callback_ms: Math.round(callbackMs),
              callback_lag_ms: Math.round(callbackLagMs),
              buffered_seconds: Number(bufferedSecondsRef.current.toFixed(3)),
            })
          }
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
            lastAudiblePositionRef.current = {
              sceneNumber: item.sceneNumber, utteranceIndex: item.utteranceIndex, sourceOffset: item.sourceOffset,
            }
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
        failPlayback(t('analysis:player.genericError'))
        return
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
    } else if (wsRef.current?.readyState === WebSocket.OPEN && bufferWaitStartedRef.current === undefined) {
      bufferWaitStartedRef.current = performance.now()
    }
  }, [failPlayback, getAudioContext, onPlaybackPositionChange, onPlayingChange, t])

  useEffect(() => {
    playNextRef.current = playNextInQueue
  }, [playNextInQueue])

  const startPlayback = useCallback((sceneNumber: number, utteranceIndex = 0, sourceOffset?: number) => {
    if (deliveryTooLong) return
    const requestedNarratorStyle = narratorStyle.trim()
    const previousSocket = wsRef.current
    wsRef.current = null
    previousSocket?.close()
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
    lastAudiblePositionRef.current = playbackPosition
    const playbackDraft = draft && sourceOffset !== undefined
      ? { ...draft, source_fountain: playbackSourceAtPosition(draft.source_fountain, playbackPosition) }
      : draft
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const path = playbackDraft ? '/api/v1/documents/playback' : `/api/v1/documents/${documentId}/playback`
    const wsUrl = `${protocol}//${window.location.host}${path}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws
    let terminalReceived = false
    let lastBufferId = 0

    const acknowledgeWhenConsumed = (bufferId: unknown) => {
      if (!Number.isSafeInteger(bufferId) || Number(bufferId) <= lastBufferId) {
        throw new Error('invalid_buffer_id')
      }
      lastBufferId = Number(bufferId)
      return () => {
        if (wsRef.current === ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'audio_consumed', buffer_id: bufferId }))
        }
      }
    }
    const canBuffer = (duration: number) => {
      if (
        scheduledSourcesRef.current.size + audioQueueRef.current.length >= MAX_BUFFERED_ITEMS
        || bufferedSecondsRef.current + duration > MAX_BUFFERED_SECONDS + 0.001
      ) {
        console.warn('Playback stopped reason=buffer_limit')
        failPlayback(t('analysis:player.bufferLimitError'))
        return false
      }
      return true
    }

    const enqueueAudioChunk = (bytes: Uint8Array, acknowledge: () => void) => {
      const context = getAudioContext()
      const meta = currentUtteranceMetaRef.current
      const byteLength = bytes.byteLength
      if (!context || !meta || byteLength === 0 || byteLength % 2 !== 0) throw new Error('invalid_pcm_buffer')
      if (!canBuffer(byteLength / 48000)) return
      const decodeStarted = performance.now()
      const buffer = pcm16ToAudioBuffer([bytes], context, 24000)
      const decodeMs = performance.now() - decodeStarted
      if (decodeMs >= 100) console.warn('Playback audio decode delayed', { decode_ms: Math.round(decodeMs) })
      const waitStarted = bufferWaitStartedRef.current
      if (waitStarted !== undefined) {
        const waitMs = performance.now() - waitStarted
        if (waitMs >= 250) {
          console.info('Playback audio wait ended', {
            wait_ms: Math.round(waitMs), decode_ms: Math.round(decodeMs),
            release_ms: Math.round(lastReleaseTimingRef.current.releaseMs),
            callback_lag_ms: Math.round(lastReleaseTimingRef.current.callbackLagMs),
          })
        }
        bufferWaitStartedRef.current = undefined
      }
      bufferedSecondsRef.current += buffer.length / buffer.sampleRate
      audioQueueRef.current.push({
        kind: 'speech',
        sceneNumber: meta.sceneNumber,
        utteranceIndex: meta.utteranceIndex,
        speaker: meta.speaker,
        sourceOffset: playbackDraft && meta.sceneNumber === sceneNumber && meta.utteranceIndex === utteranceIndex
          ? sourceOffset : undefined,
        buffer,
        acknowledge,
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
          currentUtteranceMetaRef.current = {
            speaker: String(msg.speaker ?? ''),
            sceneNumber: Number(msg.scene_number),
            utteranceIndex: Number(msg.utterance_index),
          }
        } else if (ev === 'scene_pause' || ev === 'utterance_pause') {
          const ctx = getAudioContext()
          if (ctx) {
            const durationMs = Number(msg.duration_ms)
            if (!Number.isFinite(durationMs) || durationMs < 0) throw new Error('invalid_pause')
            const acknowledge = acknowledgeWhenConsumed(msg.buffer_id)
            if (!canBuffer(durationMs / 1000)) return
            const frameCount = Math.max(1, Math.round(ctx.sampleRate * durationMs / 1000))
            const buffer = ctx.createBuffer(1, frameCount, ctx.sampleRate)
            bufferedSecondsRef.current += buffer.length / buffer.sampleRate
            audioQueueRef.current.push({
              kind: 'pause', sceneNumber: Number(msg.scene_number), buffer, acknowledge,
            })
            playNextInQueue()
          }
        } else if (ev === 'audio_chunk') {
          if (typeof msg.data !== 'string' || msg.data.length > Math.ceil(MAX_PCM_BUFFER_BYTES / 3) * 4) {
            failPlayback(t('analysis:player.bufferLimitError'))
            return
          }
          if (msg.data) {
            const acknowledge = acknowledgeWhenConsumed(msg.buffer_id)
            enqueueAudioChunk(base64ToUint8Array(msg.data), acknowledge)
          }
        } else if (ev === 'audio_segment_end') {
          // PCMはサーバーでsample境界へ整列済み。終端で再投入しない。
          return
        } else if (ev === 'utterance_end') {
          currentUtteranceMetaRef.current = undefined
        } else if (ev === 'playback_complete') {
          terminalReceived = true
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
          terminalReceived = true
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
      } catch {
        console.warn('Playback stopped reason=invalid_message')
        failPlayback(t('analysis:player.connectionError'))
      }
    }

    ws.onerror = () => {
      if (wsRef.current !== ws) return
      failPlayback(t('analysis:player.connectionError'))
    }

    ws.onclose = (event) => {
      if (wsRef.current !== ws) return
      wsRef.current = null
      if (!terminalReceived) {
        console.warn('Playback disconnected', { code: event?.code ?? 0, clean: event?.wasClean ?? false })
        failPlayback(t('analysis:player.connectionError'))
      }
    }
  }, [failPlayback, deliveryTooLong, narratorStyle, documentId, draft, getAudioContext, onPlaybackPositionChange, onPlayingChange, playNextInQueue, stopAudio, t, tErrors])

  const stopPlayback = useCallback(() => {
    const ws = wsRef.current
    wsRef.current = null
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
      startPlayback(failedPosition.scene, failedPosition.utterance, failedPosition.sourceOffset)
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
