import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Scene } from '@/api/contracts'
import { createLocaleRuntime, LocaleProvider } from '@/i18n/LocaleProvider'
import { AudiobookPlayer } from './AudiobookPlayer'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  url: string
  readyState: number = WebSocket.OPEN
  sentMessages: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    setTimeout(() => {
      this.onopen?.()
    }, 0)
  }

  send(data: string) {
    this.sentMessages.push(data)
  }

  close() {
    this.readyState = WebSocket.CLOSED
    this.onclose?.()
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
}

class MockAudioContext {
  static instances: MockAudioContext[] = []
  static starts: string[] = []
  static startedBufferLengths: number[] = []
  static startTimes: number[] = []
  static activeSources: Array<{ buffer: AudioBuffer | null; onended: (() => void) | null; stop: () => void; disconnect: () => void }> = []
  currentTime = 1
  state: AudioContextState = 'running'
  sampleRate = 24000
  destination = {}

  constructor() { MockAudioContext.instances.push(this) }

  createBuffer(_channels: number, length: number, sampleRate: number) {
    return {
      getChannelData: () => new Float32Array(length),
      length,
      sampleRate,
    } as unknown as AudioBuffer
  }

  createBufferSource() {
    const source = {
      buffer: null,
      connect: () => undefined,
      onended: null,
      disconnect: vi.fn(),
      start: (when = 0) => {
        MockAudioContext.startTimes.push(when)
        MockAudioContext.starts.push(this.state)
        MockAudioContext.startedBufferLengths.push((source.buffer as AudioBuffer | null)?.length ?? -1)
        MockAudioContext.activeSources.push(source)
      },
      stop: vi.fn(),
    }
    return source as unknown as AudioBufferSourceNode
  }

  static finishCurrentSource() {
    this.activeSources.shift()?.onended?.()
  }

  close() {
    this.state = 'closed'
    return Promise.resolve()
  }

  resume() {
    this.state = 'running'
    return Promise.resolve()
  }
}

describe('AudiobookPlayer', () => {
  it('8分の先行受信でも予約ノードを制限し、音声を欠落・重複なく最後まで補充する', async () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ documentId: 'doc-long', isPlaying: false, onPlayingChange, scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    const pcm = btoa(String.fromCharCode(...new Uint8Array(48000))) // 1秒の合成PCM。
    act(() => {
      ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
      for (let i = 0; i < 480; i++) ws.emitMessage({ event: 'audio_chunk', data: pcm })
      ws.emitMessage({ event: 'utterance_end' })
      ws.emitMessage({ event: 'playback_complete' })
    })
    expect(MockAudioContext.activeSources.length).toBeLessThanOrEqual(32)
    expect(onPlayingChange).not.toHaveBeenCalledWith(false)
    for (let i = 0; i < 480; i++) {
      MockAudioContext.instances[0].currentTime = i + 2
      act(() => MockAudioContext.finishCurrentSource())
      expect(MockAudioContext.activeSources.length).toBeLessThanOrEqual(32)
    }
    expect(MockAudioContext.startTimes).toEqual(Array.from({ length: 480 }, (_, i) => i + 1))
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)
  })

  it('先着PCMは終了通知を待たず音声時計で隙間なく予約する', async () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ documentId: 'doc-continuous', isPlaying: false, onPlayingChange, scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    const context = MockAudioContext.instances[0]
    const pcm = btoa(String.fromCharCode(...new Uint8Array(960))) // 24kHzで20ms。
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', data: pcm })
    context.currentTime = 1.005
    ws.emitMessage({ event: 'audio_chunk', data: pcm })
    context.currentTime = 1.01
    ws.emitMessage({ event: 'audio_chunk', data: pcm })
    // onendedがメインスレッドで遅れても、受信済みの次chunkは既に予約済み。
    expect(MockAudioContext.startTimes).toEqual([1, 1.02, 1.04])
    ws.emitMessage({ event: 'utterance_end' })
    ws.emitMessage({ event: 'playback_complete' })
    context.currentTime = 1.2
    MockAudioContext.finishCurrentSource()
    expect(onPlayingChange).not.toHaveBeenCalledWith(false)
    MockAudioContext.finishCurrentSource()
    expect(onPlayingChange).not.toHaveBeenCalledWith(false)
    MockAudioContext.finishCurrentSource()
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)
  })

  it('受信が途切れた後は過去の予約時刻で音声を重ねない', async () => {
    renderPlayer({ documentId: 'doc-starved', isPlaying: false, onPlayingChange: vi.fn(), scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', data: 'AAA=' })
    MockAudioContext.finishCurrentSource()
    MockAudioContext.instances[0].currentTime = 1.5
    ws.emitMessage({ event: 'audio_chunk', data: 'AAA=' })
    expect(MockAudioContext.startTimes).toEqual([1, 1.5])
  })

  it('先行予約した次話者を受信時にハイライトせず、停止で全予約を解除する', async () => {
    const onPlaybackPositionChange = vi.fn()
    const onPlayingChange = vi.fn()
    const props = { documentId: 'doc-scheduled', onPlayingChange, onPlaybackPositionChange, scenes }
    const runtime = createLocaleRuntime({ initialLocale: 'ja' })
    const { rerender } = render(
      <LocaleProvider runtime={runtime}><AudiobookPlayer {...props} isPlaying={false} /></LocaleProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    vi.useFakeTimers()
    const ws = MockWebSocket.instances[0]
    const pcm = btoa(String.fromCharCode(...new Uint8Array(960)))
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', data: pcm })
    ws.emitMessage({ event: 'utterance_end' })
    ws.emitMessage({ event: 'utterance_start', speaker: 'Child', scene_number: 1, utterance_index: 1 })
    ws.emitMessage({ event: 'audio_chunk', data: pcm })
    expect(MockAudioContext.starts).toHaveLength(2)
    expect(onPlaybackPositionChange).not.toHaveBeenCalledWith({ sceneNumber: 1, utteranceIndex: 1 })
    // 壁時計だけ進んでも、停止中のAudioContextより先に表示を進めない。
    act(() => vi.advanceTimersByTime(21))
    expect(onPlaybackPositionChange).not.toHaveBeenCalledWith({ sceneNumber: 1, utteranceIndex: 1 })
    MockAudioContext.instances[0].currentTime = 1.02
    act(() => vi.advanceTimersByTime(21))
    expect(onPlaybackPositionChange).toHaveBeenLastCalledWith({ sceneNumber: 1, utteranceIndex: 1 })
    const sources = [...MockAudioContext.activeSources]
    const staleCallbacks = sources.map((source) => source.onended)
    rerender(<LocaleProvider runtime={runtime}><AudiobookPlayer {...props} isPlaying stopSignal={1} /></LocaleProvider>)
    // stopSignalの初期値からの変更で全sourceを止める。
    for (const source of sources) expect(source.stop).toHaveBeenCalledOnce()
    const callsAfterStop = onPlaybackPositionChange.mock.calls.length
    act(() => {
      staleCallbacks.forEach((callback) => callback?.())
      vi.runOnlyPendingTimers()
    })
    expect(onPlaybackPositionChange).toHaveBeenCalledTimes(callsAfterStop)
  })

  it('PCM再生が始まるまで本文位置をハイライトしない', async () => {
    const startCountsAtPosition: number[] = []
    const onPlaybackPositionChange = vi.fn((position?: { sceneNumber: number; utteranceIndex: number }) => {
      if (position) startCountsAtPosition.push(MockAudioContext.starts.length)
    })
    renderPlayer({
      documentId: 'doc-123',
      isPlaying: false,
      onPlayingChange: vi.fn(),
      onPlaybackPositionChange,
      scenes,
    })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    const position = { sceneNumber: 1, utteranceIndex: 0 }
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    expect(onPlaybackPositionChange).not.toHaveBeenCalledWith(position)
    ws.emitMessage({ event: 'audio_chunk', scene_number: 1, utterance_index: 0, data: 'AAE=' })
    // 文の終端が来る前に、最初のPCMで実再生を開始する。
    await waitFor(() => expect(MockAudioContext.starts).toHaveLength(1))
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 1, utterance_index: 0 })
    expect(MockAudioContext.starts).toHaveLength(1)

    expect(startCountsAtPosition).toEqual([1])
  })

  it('chunk境界のPCM16を保持し、segment終端で音声を二重に入れない', async () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ documentId: 'doc-pcm', isPlaying: false, onPlayingChange, scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 1 })
    ws.emitMessage({ event: 'audio_chunk', data: 'AQ==' })
    expect(MockAudioContext.starts).toHaveLength(0)
    ws.emitMessage({ event: 'audio_chunk', data: 'AgME' })
    expect(MockAudioContext.startedBufferLengths).toEqual([2])
    ws.emitMessage({ event: 'audio_chunk', data: 'BQY=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 1, utterance_index: 1 })
    MockAudioContext.finishCurrentSource()
    expect(MockAudioContext.startedBufferLengths).toEqual([2, 1])
    ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 1 })
    ws.emitMessage({ event: 'playback_complete' })
    MockAudioContext.finishCurrentSource()
    await waitFor(() => expect(onPlayingChange).toHaveBeenLastCalledWith(false))
    expect(MockAudioContext.startedBufferLengths).toEqual([2, 1])
  })

  it('server voice errors use the current locale and preserve the failed state', async () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ disabled: false, documentId: 'doc-voice-expired', isPlaying: false, onPlayingChange, scenes })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    MockWebSocket.instances[0].emitMessage({
      event: 'error',
      code: 'VOICE_EXPIRED',
      scene_number: null,
      utterance_index: null,
      message: 'provider message should not drive UI locale',
    })

    expect(await screen.findByText('この声の有効期限が切れています。別の声を割り当ててください。')).toBeInTheDocument()
  })

  const scenes: Scene[] = [
    { scene_number: 1, heading: 'INT. ROOM - NIGHT', text: '...', dialogues: [] },
    { scene_number: 2, heading: 'INT. GARDEN - DAY', text: '...', dialogues: [] },
  ]

  beforeEach(() => {
    MockWebSocket.instances = []
    MockAudioContext.instances = []
    MockAudioContext.starts = []
    MockAudioContext.startedBufferLengths = []
    MockAudioContext.startTimes = []
    MockAudioContext.activeSources = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.stubGlobal('AudioContext', MockAudioContext)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  const renderPlayer = (props: ComponentProps<typeof AudiobookPlayer>) => render(
    <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AudiobookPlayer {...props} />
    </LocaleProvider>,
  )

  it('語り口は開始時に固定し、変更だけでは送信せず、解除・次回開始へ反映する', async () => {
    renderPlayer({ documentId: 'delivery-doc', isPlaying: false, onPlayingChange: vi.fn(), scenes })
    const input = screen.getByLabelText('ナレーターの語り口')
    fireEvent.change(input, { target: { value: '  Calm and unhurried  ' } })
    expect(MockWebSocket.instances).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    expect(input).toBeDisabled()
    await waitFor(() => expect(MockWebSocket.instances[0].sentMessages).toHaveLength(1))
    expect(JSON.parse(MockWebSocket.instances[0].sentMessages[0]).narrator_style).toBe('Calm and unhurried')
    act(() => MockWebSocket.instances[0].emitMessage({ event: 'error', code: 'TTS_FAILED', scene_number: 1, utterance_index: 1 }))
    expect(input).toBeEnabled()
    fireEvent.change(input, { target: { value: '明るく軽快に' } })
    fireEvent.click(screen.getByRole('button', { name: /再試行/ }))
    await waitFor(() => expect(MockWebSocket.instances[1].sentMessages).toHaveLength(1))
    expect(JSON.parse(MockWebSocket.instances[1].sentMessages[0]).narrator_style).toBe('明るく軽快に')
    expect(screen.getByRole('button', { name: 'クリア' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'クリア' }))
    expect(input).toHaveValue('明るく軽快に')
    act(() => MockWebSocket.instances[1].emitMessage({
      event: 'error', code: 'TTS_FAILED', scene_number: 1, utterance_index: 1,
    }))
    fireEvent.click(screen.getByRole('button', { name: 'クリア' }))
    expect(input).toHaveValue('')
    expect(MockWebSocket.instances[1].sentMessages).toHaveLength(1)
  })

  it('再生開始直後から位置の領域を確保し、Clearを無効にする', () => {
    renderPlayer({ documentId: 'pause-position', isPlaying: true, onPlayingChange: vi.fn(), scenes })
    expect(screen.getByTestId('playback-position')).toBeInTheDocument()
    expect(screen.getByLabelText('ナレーターの語り口')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'クリア' })).toBeDisabled()
  })

  it('Unicodeの文字数で500文字超を通信前に止める', () => {
    renderPlayer({ documentId: 'delivery-limit', isPlaying: false, onPlayingChange: vi.fn(), scenes })
    fireEvent.change(screen.getByLabelText('ナレーターの語り口'), { target: { value: '😀'.repeat(501) } })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    expect(MockWebSocket.instances).toHaveLength(0)
    expect(screen.getByText('語り口は500文字以内で入力してください。')).toBeInTheDocument()
  })

  it('作品を切り替えたら語り口を消去する', () => {
    const props = { documentId: 'first', isPlaying: false, onPlayingChange: vi.fn(), scenes }
    const { rerender } = renderPlayer(props)
    fireEvent.change(screen.getByLabelText('ナレーターの語り口'), { target: { value: 'calm' } })
    rerender(<LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AudiobookPlayer {...props} documentId="second" />
    </LocaleProvider>)
    expect(screen.getByLabelText('ナレーターの語り口')).toHaveValue('')
    rerender(<LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AudiobookPlayer {...props} />
    </LocaleProvider>)
    expect(screen.getByLabelText('ナレーターの語り口')).toHaveValue('')
  })

  it('renders a scene number input and play button', () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ disabled: false, documentId: 'doc-123', isPlaying: false, onPlayingChange, scenes })

    expect(screen.queryByText('朗読プレイヤー')).not.toBeInTheDocument()
    expect(screen.queryByText('待機中')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '再生開始' })).toBeInTheDocument()

    expect(screen.getByRole('spinbutton', { name: '再生開始シーン番号' })).toHaveValue(1)
  })

  it('jumps to the entered scene number', () => {
    const onSceneJump = vi.fn()
    renderPlayer({ disabled: false, documentId: 'doc-123', isPlaying: false, onPlayingChange: vi.fn(), onSceneJump, scenes })

    const input = screen.getByRole('spinbutton', { name: '再生開始シーン番号' })
    fireEvent.change(input, { target: { value: '2' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSceneJump).toHaveBeenCalledWith(2)
  })

  it('renders player controls in English when the locale is English', () => {
    render(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'en' })}>
        <AudiobookPlayer disabled={false} documentId="doc-123" isPlaying={false} onPlayingChange={vi.fn()} scenes={scenes} />
      </LocaleProvider>,
    )

    expect(screen.queryByText('Audiobook player')).not.toBeInTheDocument()
    expect(screen.getByText('Scene number')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start playback' })).toBeInTheDocument()
    expect(screen.queryByText('Idle')).not.toBeInTheDocument()
  })

  it('connects to WebSocket and sends start action on play button click', async () => {
    const onPlayingChange = vi.fn()
    renderPlayer({ disabled: false, documentId: 'doc-123', isPlaying: false, onPlayingChange, scenes })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    expect(onPlayingChange).toHaveBeenCalledWith(true)

    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBe(1)
    })

    const ws = MockWebSocket.instances[0]
    await waitFor(() => {
      expect(ws.sentMessages.length).toBe(1)
    })

    const sent = JSON.parse(ws.sentMessages[0])
    expect(sent).toEqual({
      action: 'start',
      scene_number: 1,
      start_utterance_index: 0,
    })
  })

  it('shows a spinner until the first audio buffer starts playing', async () => {
    const onPlayingChange = vi.fn()
    const props = { documentId: 'doc-123', onPlayingChange, scenes }
    const runtime = createLocaleRuntime({ initialLocale: 'ja' })
    const { rerender } = render(
      <LocaleProvider runtime={runtime}>
        <AudiobookPlayer {...props} isPlaying={false} />
      </LocaleProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    expect(onPlayingChange).toHaveBeenCalledWith(true)
    rerender(
      <LocaleProvider runtime={runtime}>
        <AudiobookPlayer {...props} isPlaying />
      </LocaleProvider>,
    )
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))

    expect(screen.getByRole('status')).toHaveTextContent('最初の音声を準備中…')

    const ws = MockWebSocket.instances[0]
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', data: 'AQIDBA==' })
    ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 0 })

    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(screen.getByText('Narrator')).toBeInTheDocument()
  })

  it('plays sentence and utterance pauses before the next scene heading', async () => {
    const onPlaybackPositionChange = vi.fn()
    renderPlayer({ documentId: 'doc-123', isPlaying: false, onPlayingChange: vi.fn(), onPlaybackPositionChange, scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    const ws = MockWebSocket.instances[0]

    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', scene_number: 1, utterance_index: 0, data: 'AAE=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 1, utterance_index: 0 })

    await waitFor(() => {
      expect(MockAudioContext.starts).toHaveLength(1)
    })

    vi.useFakeTimers()
    ws.emitMessage({ event: 'utterance_pause', scene_number: 1, duration_ms: 1000 })
    ws.emitMessage({ event: 'audio_chunk', scene_number: 1, utterance_index: 0, data: 'AQI=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 1, utterance_index: 0 })
    // pauseも音声時計で先行予約し、直前sourceの終了通知を待たない。
    expect(MockAudioContext.starts).toHaveLength(3)
    expect(MockAudioContext.startedBufferLengths[1]).toBe(24000)
    const sample = 1 / 24000
    expect(MockAudioContext.startTimes[1]).toBeCloseTo(1 + sample, 8)
    expect(MockAudioContext.startTimes[2]).toBeCloseTo(2 + sample, 8)
    const positionArea = screen.getByTestId('playback-position')
    expect(positionArea).toHaveTextContent('Narrator')
    MockAudioContext.instances[0].currentTime = 1.002
    act(() => vi.advanceTimersByTime(10))
    expect(screen.getByTestId('playback-position')).toBe(positionArea)
    expect(positionArea).toHaveTextContent('Narrator')
    expect(onPlaybackPositionChange).toHaveBeenLastCalledWith(undefined)

    ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'scene_pause', scene_number: 2, duration_ms: 100 })
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 2, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', scene_number: 2, utterance_index: 0, data: 'AAE=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 2, utterance_index: 0 })
    ws.emitMessage({ event: 'utterance_end', scene_number: 2, utterance_index: 0 })

    // 100msの場面pauseを保持し、次の見出しの表示だけ実再生時刻まで待つ。
    expect(MockAudioContext.starts).toHaveLength(5)
    expect(MockAudioContext.startedBufferLengths[3]).toBe(2400)
    expect(MockAudioContext.startTimes[3]).toBeCloseTo(2 + 2 * sample, 8)
    expect(MockAudioContext.startTimes[4]).toBeCloseTo(2.1 + 2 * sample, 8)
    expect(onPlaybackPositionChange).not.toHaveBeenCalledWith({ sceneNumber: 2, utteranceIndex: 0 })
    MockAudioContext.instances[0].currentTime = 2.102
    act(() => vi.advanceTimersByTime(1102))
    expect(onPlaybackPositionChange).toHaveBeenCalledWith({ sceneNumber: 2, utteranceIndex: 0 })
  })

  it('starts playback from the current script cursor position', async () => {
    renderPlayer({
      disabled: false,
      documentId: 'doc-123',
      isPlaying: false,
      onPlayingChange: vi.fn(),
      playbackStart: { sceneNumber: 2, utteranceIndex: 1 },
      scenes,
    })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws = MockWebSocket.instances[0]
    await waitFor(() => expect(ws.sentMessages.length).toBe(1))

    expect(JSON.parse(ws.sentMessages[0])).toEqual({
      action: 'start',
      scene_number: 2,
      start_utterance_index: 1,
    })
  })

  it('未保存原稿はカーソルの文から同じ発話 index で再生する', async () => {
    const source = [
      'INT. ROOM - DAY #1#',
      '',
      '@ナレーター',
      '雨の匂いがした。',
      '窓辺に小さな傘が置かれていた。',
    ].join('\n')
    const sentenceStart = source.indexOf('窓辺')
    const onPlaybackPositionChange = vi.fn()
    const draft = {
      source_fountain: source,
      voice_assignments: [{ speaker: 'ナレーター', voice_id: 'voices/synthetic', voice_traits: '落ち着いた声' }],
    }
    renderPlayer({
      documentId: 'draft-only-id', draft, isPlaying: false, onPlayingChange: vi.fn(), onPlaybackPositionChange,
      playbackStart: { sceneNumber: 1, utteranceIndex: 1, sourceOffset: sentenceStart }, scenes,
    })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))

    expect(JSON.parse(MockWebSocket.instances[0].sentMessages[0])).toEqual({
      action: 'start',
      scene_number: 1,
      start_utterance_index: 1,
      draft: {
        ...draft,
        source_fountain: [
          'INT. ROOM - DAY #1#',
          '',
          '@ナレーター',
          '窓辺に小さな傘が置かれていた。',
        ].join('\n'),
      },
    })
    const ws = MockWebSocket.instances[0]
    ws.emitMessage({ event: 'utterance_start', speaker: 'ナレーター', scene_number: 1, utterance_index: 1 })
    ws.emitMessage({ event: 'audio_chunk', data: 'AAA=' })
    expect(onPlaybackPositionChange).toHaveBeenLastCalledWith({
      sceneNumber: 1, utteranceIndex: 1, sourceOffset: sentenceStart,
    })
  })

  it('plays an unsaved import using its current script and voice assignments', async () => {
    const draft = {
      source_fountain: 'INT. ROOM - DAY #1#\n\n@Narrator\nHello.',
      voice_assignments: [{ speaker: 'Narrator', voice_id: 'voices/synthetic', voice_traits: 'Calm' }],
    }
    renderPlayer({
      documentId: 'draft-only-id', draft, isPlaying: false, onPlayingChange: vi.fn(),
      playbackStart: { sceneNumber: 2, utteranceIndex: 1 }, scenes,
    })

    expect(screen.getByRole('spinbutton', { name: '再生開始シーン番号' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))

    const ws = MockWebSocket.instances[0]
    expect(ws.url).toMatch(/\/api\/v1\/documents\/playback$/)
    expect(JSON.parse(ws.sentMessages[0])).toEqual({
      action: 'start', scene_number: 2, start_utterance_index: 1, draft,
    })
  })

  it('再生ボタンの操作中に音声出力を準備する', () => {
    renderPlayer({ documentId: 'draft', isPlaying: false, onPlayingChange: vi.fn(), scenes })
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    expect(MockAudioContext.instances).toHaveLength(1)
  })

  it('閉じた音声出力を再利用せず、下書きの受信音声を再生する', async () => {
    renderPlayer({
      documentId: 'draft', isPlaying: false, onPlayingChange: vi.fn(), scenes,
      draft: { source_fountain: 'INT. ROOM - DAY\n\n@Narrator\nHello.', voice_assignments: [] },
    })
    const emitAudio = (ws: MockWebSocket) => {
      ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 1, utterance_index: 0 })
      ws.emitMessage({ event: 'audio_chunk', data: 'AQIDBA==' })
      ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 0 })
    }
    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[0]?.sentMessages).toHaveLength(1))
    emitAudio(MockWebSocket.instances[0])
    await MockAudioContext.instances[0].close()

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    await waitFor(() => expect(MockWebSocket.instances[1]?.sentMessages).toHaveLength(1))
    emitAudio(MockWebSocket.instances[1])

    expect(MockAudioContext.starts).toEqual(['running', 'running'])
    expect(MockAudioContext.instances).toHaveLength(2)
  })

  it('stops when an external document operation changes the stop signal', async () => {
    const onPlayingChange = vi.fn()
    const { rerender } = renderPlayer({
      disabled: false,
      documentId: 'doc-123',
      isPlaying: true,
      onPlayingChange,
      scenes,
      stopSignal: 0,
    })

    rerender(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <AudiobookPlayer disabled={false} documentId="doc-123" isPlaying={false} onPlayingChange={onPlayingChange} scenes={scenes} stopSignal={1} />
      </LocaleProvider>,
    )

    await waitFor(() => expect(onPlayingChange).toHaveBeenCalledWith(false))
  })

  it('handles playback events and stops cleanly', async () => {
    const onPlayingChange = vi.fn()
    const { rerender } = renderPlayer({
      disabled: false,
      documentId: 'doc-123',
      isPlaying: false,
      onPlayingChange,
      scenes,
    })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    rerender(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <AudiobookPlayer
        disabled={false}
        documentId="doc-123"
        isPlaying={true}
        onPlayingChange={onPlayingChange}
        scenes={scenes}
        />
      </LocaleProvider>
    )

    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws = MockWebSocket.instances[0]

    // Server emits utterance_start
    ws.emitMessage({
      event: 'utterance_start',
      scene_number: 1,
      utterance_index: 0,
      speaker: 'ナレーター',
      target_type: 'narrator',
    })

    ws.emitMessage({ event: 'audio_chunk', scene_number: 1, utterance_index: 0, data: 'AAE=' })
    ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 0 })

    await waitFor(() => expect(screen.getByRole('button', { name: '再生停止' })).toBeInTheDocument())
    expect(screen.getByText('ナレーター')).toBeInTheDocument()
    expect(screen.getByText('(シーン 1 発話 #1)')).toBeInTheDocument()

    // Stop button works
    const stopBtn = screen.getByRole('button', { name: '再生停止' })
    fireEvent.click(stopBtn)

    expect(ws.sentMessages).toContain(JSON.stringify({ action: 'stop' }))
    expect(onPlayingChange).toHaveBeenCalledWith(false)
  })

  it('displays error and retry button when playback fails', async () => {
    const onPlayingChange = vi.fn()
    const { rerender } = renderPlayer({ disabled: false, documentId: 'doc-123', isPlaying: false, onPlayingChange, scenes })

    fireEvent.click(screen.getByRole('button', { name: '再生開始' }))
    rerender(
      <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
        <AudiobookPlayer disabled={false} documentId="doc-123" isPlaying={true} onPlayingChange={onPlayingChange} scenes={scenes} />
      </LocaleProvider>,
    )

    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws = MockWebSocket.instances[0]

    // Server emits error
    ws.emitMessage({
      event: 'error',
      scene_number: 1,
      utterance_index: 2,
      message: 'TTS generation failed for character Bob',
    })

    await waitFor(() => {
      expect(screen.getByText(/TTS generation failed for character Bob/)).toBeInTheDocument()
      expect(screen.getByText(/シーン 1 発話 #3/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '失敗位置から再試行' })).toBeInTheDocument()
    })

    // Click retry
    fireEvent.click(screen.getByRole('button', { name: '失敗位置から再試行' }))

    // New WebSocket instance created for retry from failed position
    await waitFor(() => expect(MockWebSocket.instances.length).toBe(2))
    const retryWs = MockWebSocket.instances[1]
    await waitFor(() => expect(retryWs.sentMessages.length).toBe(1))

    const retrySent = JSON.parse(retryWs.sentMessages[0])
    expect(retrySent).toEqual({
      action: 'start',
      scene_number: 1,
      start_utterance_index: 2,
    })
  })
})
