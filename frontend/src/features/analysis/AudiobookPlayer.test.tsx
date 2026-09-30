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
  state = 'running'
  sampleRate = 24000
  destination = {}

  createBuffer(_channels: number, length: number, sampleRate: number) {
    return {
      getChannelData: () => new Float32Array(length),
      length,
      sampleRate,
    } as unknown as AudioBuffer
  }

  createBufferSource() {
    return {
      buffer: null,
      connect: () => undefined,
      onended: null,
      start: () => undefined,
      stop: () => undefined,
    } as unknown as AudioBufferSourceNode
  }

  close() {
    return Promise.resolve()
  }

  resume() {
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
