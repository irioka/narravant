import { describe, expect, it } from 'vitest'
import { base64ToUint8Array, pcm16ToAudioBuffer } from './audio-decoder'

describe('audio-decoder', () => {
  it('converts base64 to Uint8Array', () => {
    const raw = 'SGVsbG8=' // "Hello"
    const bytes = base64ToUint8Array(raw)
    expect(bytes).toEqual(new Uint8Array([72, 101, 108, 108, 111]))
  })

  it('converts 16-bit PCM chunks to normalized AudioBuffer', () => {
    // Mock AudioContext
    const mockChannelData = new Float32Array(2)
    const mockAudioBuffer = {
      getChannelData: () => mockChannelData,
    } as unknown as AudioBuffer

    const mockContext = {
      createBuffer: (_channels: number, length: number, sampleRate: number) => {
        expect(length).toBe(2)
        expect(sampleRate).toBe(24000)
        return mockAudioBuffer
      },
    } as unknown as AudioContext

    // Create 2 16-bit samples: 0 and 32767
    const buffer = new ArrayBuffer(4)
    const view = new DataView(buffer)
    view.setInt16(0, 0, true)
    view.setInt16(2, 32767, true)

    const chunk = new Uint8Array(buffer)
    const result = pcm16ToAudioBuffer([chunk], mockContext, 24000)
    expect(result).toBe(mockAudioBuffer)
    expect(mockChannelData[0]).toBe(0)
    expect(mockChannelData[1]).toBeCloseTo(1.0, 4)
  })
})
