import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  static activeSources: Array<{ buffer: AudioBuffer | null; onended: (() => void) | null }> = []
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
      start: () => {
        MockAudioContext.starts.push(this.state)
        MockAudioContext.startedBufferLengths.push((source.buffer as AudioBuffer | null)?.length ?? -1)
        MockAudioContext.activeSources.push(source)
      },
      stop: () => undefined,
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
  const scenes: Scene[] = [
    { scene_number: 1, heading: 'INT. ROOM - NIGHT', text: '...', dialogues: [] },
    { scene_number: 2, heading: 'INT. GARDEN - DAY', text: '...', dialogues: [] },
  ]

  beforeEach(() => {
    MockWebSocket.instances = []
    MockAudioContext.instances = []
    MockAudioContext.starts = []
    MockAudioContext.startedBufferLengths = []
    MockAudioContext.activeSources = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.stubGlobal('AudioContext', MockAudioContext)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const renderPlayer = (props: ComponentProps<typeof AudiobookPlayer>) => render(
    <LocaleProvider runtime={createLocaleRuntime({ initialLocale: 'ja' })}>
      <AudiobookPlayer {...props} />
    </LocaleProvider>,
  )

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
    expect(screen.getByText('話者: Narrator')).toBeInTheDocument()
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

    ws.emitMessage({ event: 'utterance_pause', scene_number: 1, duration_ms: 1000 })
    ws.emitMessage({ event: 'audio_chunk', scene_number: 1, utterance_index: 0, data: 'AQI=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 1, utterance_index: 0 })
    expect(MockAudioContext.starts).toHaveLength(1)
    MockAudioContext.finishCurrentSource()
    await waitFor(() => expect(MockAudioContext.starts).toHaveLength(2))
    expect(MockAudioContext.startedBufferLengths[1]).toBe(24000)
    MockAudioContext.finishCurrentSource()
    await waitFor(() => expect(MockAudioContext.starts).toHaveLength(3))

    ws.emitMessage({ event: 'utterance_end', scene_number: 1, utterance_index: 0 })
    ws.emitMessage({ event: 'scene_pause', scene_number: 2, duration_ms: 100 })
    ws.emitMessage({ event: 'utterance_start', speaker: 'Narrator', scene_number: 2, utterance_index: 0 })
    ws.emitMessage({ event: 'audio_chunk', scene_number: 2, utterance_index: 0, data: 'AAE=' })
    ws.emitMessage({ event: 'audio_segment_end', scene_number: 2, utterance_index: 0 })
    ws.emitMessage({ event: 'utterance_end', scene_number: 2, utterance_index: 0 })

    // シーン1の再生中に次の見出しをキューへ入れ、完了後に100msの無音を挟む。
    expect(MockAudioContext.starts).toHaveLength(3)
    MockAudioContext.finishCurrentSource()
    expect(MockAudioContext.startedBufferLengths[3]).toBe(2400)
    MockAudioContext.finishCurrentSource()
    await waitFor(() => {
      expect(MockAudioContext.starts).toHaveLength(5)
    })
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
    expect(screen.getByText('話者: ナレーター')).toBeInTheDocument()
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
